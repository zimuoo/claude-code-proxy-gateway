from typing import Any
from urllib.parse import urlencode
import json
import uuid

import httpx
from fastapi import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from app.compat import (
    anthropic_messages_to_openai_chat,
    chat_completion_to_responses,
    extract_openai_usage,
    merge_anthropic_usage,
    openai_finish_reason_to_anthropic,
    openai_chat_to_anthropic_message,
    openai_chat_stream_chunk_to_response_events,
    responses_input_to_messages,
)
from app.http_utils import request_with_retry
from app.providers.base import ProviderAdapter


HOP_BY_HOP_HEADERS = {
    "host",
    "content-length",
    "connection",
    "transfer-encoding",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "upgrade",
}


def _join_url(base_url: str, api_prefix: str, path: str, query_params: dict[str, Any]) -> str:
    prefix = (api_prefix or "/v1").strip()
    if not prefix.startswith("/"):
        prefix = f"/{prefix}"
    if prefix == "/":
        url = f"{base_url}/{path.lstrip('/')}"
    else:
        url = f"{base_url}{prefix}/{path.lstrip('/')}"
    if query_params:
        return f"{url}?{urlencode(query_params, doseq=True)}"
    return url


class OpenAICompatibleAdapter(ProviderAdapter):
    def _build_headers(self, request: Request) -> dict[str, str]:
        headers: dict[str, str] = {}
        for k, v in request.headers.items():
            if k.lower() in HOP_BY_HOP_HEADERS:
                continue
            if k.lower() == "authorization":
                continue
            headers[k] = v

        if self.config.api_key:
            if self.config.auth_scheme.lower() == "bearer":
                headers[self.config.auth_header] = f"Bearer {self.config.api_key}"
            else:
                headers[self.config.auth_header] = self.config.api_key

        for k, v in self.config.extra_headers.items():
            headers[k] = v

        return headers

    async def handle(self, request: Request, path: str, payload: dict[str, Any] | None) -> Response:
        target_url = _join_url(
            self.config.base_url,
            self.config.api_prefix,
            path,
            dict(request.query_params),
        )
        body = await request.body()
        headers = self._build_headers(request)

        if path == "messages":
            anthropic_payload = payload or {}
            translated_payload = anthropic_messages_to_openai_chat(anthropic_payload)
            chat_url = _join_url(
                self.config.base_url,
                self.config.api_prefix,
                "chat/completions",
                dict(request.query_params),
            )
            if translated_payload.get("stream"):
                async def anthropic_stream_from_chat() -> Any:
                    message_id = f"msg_{uuid.uuid4().hex}"
                    text_block_index: int | None = None
                    next_block_index = 0
                    tool_block_index: dict[int, int] = {}
                    tool_state: dict[int, dict[str, str]] = {}
                    final_finish_reason: str | None = None
                    final_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

                    yield "event: message_start\n"
                    yield "data: " + json.dumps(
                        {
                            "type": "message_start",
                            "message": {
                                "id": message_id,
                                "type": "message",
                                "role": "assistant",
                                "model": translated_payload.get("model"),
                                "content": [],
                                "usage": final_usage,
                            },
                        },
                        ensure_ascii=False,
                    ) + "\n\n"

                    async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                        async with client.stream(
                            "POST",
                            chat_url,
                            headers=headers,
                            json=translated_payload,
                        ) as upstream:
                            async for line in upstream.aiter_lines():
                                if not line.startswith("data:"):
                                    continue
                                raw = line.removeprefix("data:").strip()
                                if not raw or raw == "[DONE]":
                                    continue
                                try:
                                    chunk = json.loads(raw)
                                except json.JSONDecodeError:
                                    continue
                                choice = (chunk.get("choices") or [{}])[0]
                                delta = choice.get("delta") or {}
                                delta_text = delta.get("content", "")
                                final_usage = merge_anthropic_usage(
                                    final_usage, extract_openai_usage(chunk.get("usage") or {})
                                )
                                if delta_text:
                                    if text_block_index is None:
                                        text_block_index = next_block_index
                                        next_block_index += 1
                                        yield "event: content_block_start\n"
                                        yield "data: " + json.dumps(
                                            {
                                                "type": "content_block_start",
                                                "index": text_block_index,
                                                "content_block": {"type": "text", "text": ""},
                                            },
                                            ensure_ascii=False,
                                        ) + "\n\n"
                                    yield "event: content_block_delta\n"
                                    yield "data: " + json.dumps(
                                        {
                                            "type": "content_block_delta",
                                            "index": text_block_index,
                                            "delta": {"type": "text_delta", "text": delta_text},
                                        },
                                        ensure_ascii=False,
                                    ) + "\n\n"

                                tool_calls_delta = delta.get("tool_calls") or []
                                for tc in tool_calls_delta:
                                    if not isinstance(tc, dict):
                                        continue
                                    tc_idx = int(tc.get("index", 0))
                                    state = tool_state.setdefault(tc_idx, {"id": "", "name": "", "args": ""})
                                    if tc.get("id"):
                                        state["id"] = tc["id"]
                                    fn = tc.get("function") or {}
                                    if isinstance(fn, dict):
                                        if fn.get("name"):
                                            state["name"] = fn["name"]
                                        if fn.get("arguments"):
                                            state["args"] += str(fn["arguments"])

                                    if tc_idx not in tool_block_index:
                                        tool_block_index[tc_idx] = next_block_index
                                        next_block_index += 1
                                        yield "event: content_block_start\n"
                                        yield "data: " + json.dumps(
                                            {
                                                "type": "content_block_start",
                                                "index": tool_block_index[tc_idx],
                                                "content_block": {
                                                    "type": "tool_use",
                                                    "id": state["id"] or f"toolu_{uuid.uuid4().hex}",
                                                    "name": state["name"] or "tool",
                                                    "input": {},
                                                },
                                            },
                                            ensure_ascii=False,
                                        ) + "\n\n"

                                    if isinstance(fn, dict) and fn.get("arguments"):
                                        yield "event: content_block_delta\n"
                                        yield "data: " + json.dumps(
                                            {
                                                "type": "content_block_delta",
                                                "index": tool_block_index[tc_idx],
                                                "delta": {
                                                    "type": "input_json_delta",
                                                    "partial_json": str(fn.get("arguments", "")),
                                                },
                                            },
                                            ensure_ascii=False,
                                        ) + "\n\n"

                                if choice.get("finish_reason"):
                                    final_finish_reason = choice.get("finish_reason")
                                    break

                    if text_block_index is not None:
                        yield "event: content_block_stop\n"
                        yield "data: " + json.dumps(
                            {"type": "content_block_stop", "index": text_block_index}, ensure_ascii=False
                        ) + "\n\n"
                    for _, idx in sorted(tool_block_index.items(), key=lambda x: x[1]):
                        yield "event: content_block_stop\n"
                        yield "data: " + json.dumps(
                            {"type": "content_block_stop", "index": idx}, ensure_ascii=False
                        ) + "\n\n"
                    yield "event: message_delta\n"
                    yield "data: " + json.dumps(
                        {
                            "type": "message_delta",
                            "delta": {
                                "stop_reason": openai_finish_reason_to_anthropic(final_finish_reason),
                                "stop_sequence": None,
                            },
                            "usage": final_usage,
                        },
                        ensure_ascii=False,
                    ) + "\n\n"
                    yield "event: message_stop\n"
                    yield "data: " + json.dumps({"type": "message_stop"}, ensure_ascii=False) + "\n\n"

                return StreamingResponse(anthropic_stream_from_chat(), media_type="text/event-stream")

            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                upstream = await request_with_retry(
                    client,
                    "POST",
                    chat_url,
                    headers=headers,
                    json_payload=translated_payload,
                    max_attempts=self.retry_max_attempts,
                    backoff_ms=self.retry_backoff_ms,
                )
            if upstream.status_code >= 400:
                return Response(
                    content=upstream.content,
                    status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type", "application/json"),
                )
            return JSONResponse(
                content=openai_chat_to_anthropic_message(
                    upstream.json(), translated_payload.get("model", "unknown")
                )
            )

        if path == "responses" and not self.config.supports_responses:
            if payload and payload.get("stream"):
                translated_payload = {
                    "model": (payload or {}).get("model"),
                    "messages": responses_input_to_messages(payload or {}),
                    "max_tokens": (payload or {}).get(
                        "max_output_tokens",
                        (payload or {}).get("max_tokens", 1024),
                    ),
                    "temperature": (payload or {}).get("temperature", 1),
                    "stream": True,
                }
                target_url = _join_url(
                    self.config.base_url,
                    self.config.api_prefix,
                    "chat/completions",
                    dict(request.query_params),
                )

                async def responses_stream_from_chat() -> Any:
                    response_id = f"resp_{uuid.uuid4().hex}"
                    yield f"data: {json.dumps({'type': 'response.created', 'response': {'id': response_id, 'model': translated_payload.get('model')}}, ensure_ascii=False)}\n\n"
                    async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                        async with client.stream(
                            request.method,
                            target_url,
                            headers=headers,
                            json=translated_payload,
                        ) as upstream:
                            async for line in upstream.aiter_lines():
                                if not line.startswith("data:"):
                                    continue
                                raw = line.removeprefix("data:").strip()
                                if not raw:
                                    continue
                                if raw == "[DONE]":
                                    yield "data: [DONE]\n\n"
                                    break
                                try:
                                    chunk = json.loads(raw)
                                except json.JSONDecodeError:
                                    continue
                                for event in openai_chat_stream_chunk_to_response_events(chunk):
                                    if event.get("type") == "response.completed":
                                        event["response_id"] = response_id
                                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

                return StreamingResponse(responses_stream_from_chat(), media_type="text/event-stream")

            translated_payload = {
                "model": (payload or {}).get("model"),
                "messages": responses_input_to_messages(payload or {}),
                "max_tokens": (payload or {}).get(
                    "max_output_tokens",
                    (payload or {}).get("max_tokens", 1024),
                ),
                "temperature": (payload or {}).get("temperature", 1),
                "stream": False,
            }
            target_url = _join_url(
                self.config.base_url,
                self.config.api_prefix,
                "chat/completions",
                dict(request.query_params),
            )
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                upstream = await request_with_retry(
                    client,
                    "POST",
                    target_url,
                    headers=headers,
                    json_payload=translated_payload,
                    max_attempts=self.retry_max_attempts,
                    backoff_ms=self.retry_backoff_ms,
                )
            if upstream.status_code >= 400:
                return Response(
                    content=upstream.content,
                    status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type", "application/json"),
                )
            return Response(
                content=json.dumps(chat_completion_to_responses(upstream.json()), ensure_ascii=False),
                status_code=200,
                media_type="application/json",
            )

        accept_value = request.headers.get("accept", "")
        content_type = request.headers.get("content-type", "")
        wants_stream = "text/event-stream" in accept_value or "stream" in content_type
        if payload and isinstance(payload.get("stream"), bool):
            wants_stream = wants_stream or payload.get("stream", False)

        if wants_stream:
            client = httpx.AsyncClient(timeout=self.timeout_seconds)
            req = client.build_request(request.method, target_url, headers=headers, content=body)
            upstream = await client.send(req, stream=True)
            response_headers = {
                k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP_HEADERS
            }

            async def stream_proxy() -> Any:
                try:
                    async for chunk in upstream.aiter_raw():
                        yield chunk
                finally:
                    await upstream.aclose()
                    await client.aclose()

            return StreamingResponse(
                stream_proxy(),
                status_code=upstream.status_code,
                headers=response_headers,
                media_type=upstream.headers.get("content-type", "text/event-stream"),
            )

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            upstream = await request_with_retry(
                client,
                request.method,
                target_url,
                headers=headers,
                content=body,
                max_attempts=self.retry_max_attempts,
                backoff_ms=self.retry_backoff_ms,
            )
        response_headers = {
            k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP_HEADERS
        }
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=upstream.headers.get("content-type"),
        )

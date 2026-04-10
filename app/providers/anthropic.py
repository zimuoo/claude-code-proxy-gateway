import json
import time
import uuid
from typing import Any

import httpx
from fastapi import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from app.compat import (
    anthropic_content_to_openai_message,
    chat_completion_to_responses,
    responses_input_to_messages,
)
from app.http_utils import request_with_retry
from app.providers.base import ProviderAdapter


def _openai_to_anthropic_messages(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            if isinstance(content, str):
                system_parts.append(content)
            continue

        if isinstance(content, list):
            items = []
            for item in content:
                item_type = item.get("type")
                if item_type == "text":
                    items.append({"type": "text", "text": item.get("text", "")})
                elif item_type == "image_url":
                    image_url = item.get("image_url", {}).get("url", "")
                    if image_url.startswith("data:") and "," in image_url:
                        mime_type, b64_data = image_url.split(",", 1)
                        mime_type = mime_type.split(";")[0].replace("data:", "")
                        items.append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": mime_type,
                                    "data": b64_data,
                                },
                            }
                        )
            converted.append({"role": role, "content": items})
        else:
            converted.append({"role": role, "content": [{"type": "text", "text": str(content)}]})

    system_prompt = "\n".join(system_parts) if system_parts else None
    return system_prompt, converted


def _anthropic_to_openai_response(resp: dict[str, Any], model: str) -> dict[str, Any]:
    text_content, tool_calls = anthropic_content_to_openai_message(resp.get("content", []))

    usage = resp.get("usage", {})
    prompt_tokens = usage.get("input_tokens", 0)
    completion_tokens = usage.get("output_tokens", 0)
    assistant_message: dict[str, Any] = {"role": "assistant", "content": text_content}
    if tool_calls:
        assistant_message["tool_calls"] = tool_calls

    return {
        "id": f"chatcmpl-{resp.get('id', uuid.uuid4().hex)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": assistant_message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


class AnthropicAdapter(ProviderAdapter):
    def _build_headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.config.api_key,
            "anthropic-version": self.config.anthropic_version,
            "content-type": "application/json",
            **self.config.extra_headers,
        }

    async def _chat_completion_data(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | bytes]:
        model = payload.get("model", "claude-sonnet-4-20250514")
        system_prompt, messages = _openai_to_anthropic_messages(payload.get("messages", []))

        anthropic_payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": payload.get("max_tokens", 1024),
            "temperature": payload.get("temperature", 1),
        }
        if system_prompt:
            anthropic_payload["system"] = system_prompt

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            upstream = await request_with_retry(
                client,
                "POST",
                f"{self.config.base_url}/v1/messages",
                headers=self._build_headers(),
                json_payload=anthropic_payload,
                max_attempts=self.retry_max_attempts,
                backoff_ms=self.retry_backoff_ms,
            )
        if upstream.status_code >= 400:
            return upstream.status_code, upstream.content
        return 200, _anthropic_to_openai_response(upstream.json(), model)

    async def _chat_completions(self, payload: dict[str, Any]) -> Response:
        model = payload.get("model", "claude-sonnet-4-20250514")
        system_prompt, messages = _openai_to_anthropic_messages(payload.get("messages", []))

        anthropic_payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": payload.get("max_tokens", 1024),
            "temperature": payload.get("temperature", 1),
        }
        if system_prompt:
            anthropic_payload["system"] = system_prompt

        stream = bool(payload.get("stream", False))
        if stream:
            anthropic_payload["stream"] = True

            async def event_stream() -> Any:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    async with client.stream(
                        "POST",
                        f"{self.config.base_url}/v1/messages",
                        headers=self._build_headers(),
                        json=anthropic_payload,
                    ) as upstream:
                        async for line in upstream.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            raw = line.removeprefix("data:").strip()
                            if not raw or raw == "[DONE]":
                                continue
                            try:
                                event = json.loads(raw)
                            except json.JSONDecodeError:
                                continue

                            event_type = event.get("type")
                            if event_type == "content_block_delta":
                                delta = event.get("delta", {}).get("text", "")
                                chunk = {
                                    "id": f"chatcmpl-{uuid.uuid4().hex}",
                                    "object": "chat.completion.chunk",
                                    "created": int(time.time()),
                                    "model": model,
                                    "choices": [
                                        {
                                            "index": 0,
                                            "delta": {"content": delta},
                                            "finish_reason": None,
                                        }
                                    ],
                                }
                                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                            elif event_type == "message_stop":
                                done = {
                                    "id": f"chatcmpl-{uuid.uuid4().hex}",
                                    "object": "chat.completion.chunk",
                                    "created": int(time.time()),
                                    "model": model,
                                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                                }
                                yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
                                yield "data: [DONE]\n\n"
                                break

            return StreamingResponse(event_stream(), media_type="text/event-stream")

        status_code, result = await self._chat_completion_data(payload)
        if status_code >= 400 and isinstance(result, bytes):
            return Response(
                content=result,
                status_code=status_code,
                media_type="application/json",
            )
        return JSONResponse(content=result)

    async def _responses(self, payload: dict[str, Any]) -> Response:
        if payload.get("stream"):
            model = payload.get("model", "claude-sonnet-4-20250514")
            pseudo_chat_payload = {
                "model": model,
                "messages": responses_input_to_messages(payload),
                "max_tokens": payload.get("max_output_tokens", payload.get("max_tokens", 1024)),
                "temperature": payload.get("temperature", 1),
                "stream": True,
            }
            system_prompt, messages = _openai_to_anthropic_messages(pseudo_chat_payload.get("messages", []))
            anthropic_payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": pseudo_chat_payload["max_tokens"],
                "temperature": pseudo_chat_payload["temperature"],
                "stream": True,
            }
            if system_prompt:
                anthropic_payload["system"] = system_prompt

            async def responses_stream() -> Any:
                response_id = f"resp_{uuid.uuid4().hex}"
                yield f"data: {json.dumps({'type': 'response.created', 'response': {'id': response_id, 'model': model}}, ensure_ascii=False)}\n\n"
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    async with client.stream(
                        "POST",
                        f"{self.config.base_url}/v1/messages",
                        headers=self._build_headers(),
                        json=anthropic_payload,
                    ) as upstream:
                        async for line in upstream.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            raw = line.removeprefix("data:").strip()
                            if not raw or raw == "[DONE]":
                                continue
                            try:
                                event = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            event_type = event.get("type")
                            if event_type == "content_block_delta":
                                delta_text = event.get("delta", {}).get("text", "")
                                if delta_text:
                                    mapped = {
                                        "type": "response.output_text.delta",
                                        "response_id": response_id,
                                        "delta": delta_text,
                                    }
                                    yield f"data: {json.dumps(mapped, ensure_ascii=False)}\n\n"
                            elif event_type == "message_stop":
                                done = {
                                    "type": "response.completed",
                                    "response_id": response_id,
                                    "model": model,
                                }
                                yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
                                yield "data: [DONE]\n\n"
                                break

            return StreamingResponse(responses_stream(), media_type="text/event-stream")

        pseudo_chat_payload = {
            "model": payload.get("model", "claude-sonnet-4-20250514"),
            "messages": responses_input_to_messages(payload),
            "max_tokens": payload.get("max_output_tokens", payload.get("max_tokens", 1024)),
            "temperature": payload.get("temperature", 1),
            "stream": False,
        }
        status_code, result = await self._chat_completion_data(pseudo_chat_payload)
        if status_code >= 400 and isinstance(result, bytes):
            return Response(content=result, status_code=status_code, media_type="application/json")
        return JSONResponse(content=chat_completion_to_responses(result))  # type: ignore[arg-type]

    async def handle(self, request: Request, path: str, payload: dict[str, Any] | None) -> Response:
        payload = payload or {}
        if path == "messages":
            stream = bool(payload.get("stream", False))
            if stream:
                async def passthrough_stream() -> Any:
                    async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                        async with client.stream(
                            "POST",
                            f"{self.config.base_url}/v1/messages",
                            headers=self._build_headers(),
                            json=payload,
                        ) as upstream:
                            async for chunk in upstream.aiter_raw():
                                yield chunk

                return StreamingResponse(passthrough_stream(), media_type="text/event-stream")

            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                upstream = await request_with_retry(
                    client,
                    "POST",
                    f"{self.config.base_url}/v1/messages",
                    headers=self._build_headers(),
                    json_payload=payload,
                    max_attempts=self.retry_max_attempts,
                    backoff_ms=self.retry_backoff_ms,
                )
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type", "application/json"),
            )

        if path == "chat/completions":
            return await self._chat_completions(payload)

        if path == "responses":
            return await self._responses(payload)

        if path == "models":
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                upstream = await request_with_retry(
                    client,
                    "GET",
                    f"{self.config.base_url}/v1/models",
                    headers=self._build_headers(),
                    max_attempts=self.retry_max_attempts,
                    backoff_ms=self.retry_backoff_ms,
                )
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type", "application/json"),
            )

        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": (
                        f"Path /v1/{path} is not implemented for anthropic adapter yet. "
                        "Use an openai_compatible provider for full endpoint passthrough."
                    ),
                    "type": "invalid_request_error",
                }
            },
        )

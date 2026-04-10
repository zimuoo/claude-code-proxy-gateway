import time
import uuid
from typing import Any
import json


def responses_input_to_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    input_value = payload.get("input", "")
    if isinstance(input_value, str):
        return [{"role": "user", "content": input_value}]

    if isinstance(input_value, list):
        messages: list[dict[str, Any]] = []
        for item in input_value:
            role = item.get("role", "user")
            content = item.get("content", "")
            messages.append({"role": role, "content": content})
        return messages

    return payload.get("messages", [])


def chat_completion_to_responses(chat_resp: dict[str, Any]) -> dict[str, Any]:
    choice = (chat_resp.get("choices") or [{}])[0]
    assistant_text = choice.get("message", {}).get("content", "")
    model = chat_resp.get("model", "unknown")
    usage = chat_resp.get("usage", {})

    return {
        "id": f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": [
            {
                "id": f"msg_{uuid.uuid4().hex}",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": assistant_text}],
            }
        ],
        "usage": usage,
    }


def anthropic_content_to_openai_message(content: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for item in content:
        item_type = item.get("type")
        if item_type == "text":
            text_parts.append(item.get("text", ""))
        elif item_type == "tool_use":
            tool_calls.append(
                {
                    "id": item.get("id", f"call_{uuid.uuid4().hex}"),
                    "type": "function",
                    "function": {
                        "name": item.get("name", "tool"),
                        "arguments": json.dumps(item.get("input", {}), ensure_ascii=False),
                    },
                }
            )

    return "".join(text_parts), tool_calls


def openai_chat_stream_chunk_to_response_events(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    chunk_id = chunk.get("id", f"chatcmpl-{uuid.uuid4().hex}")
    model = chunk.get("model", "unknown")
    choice = (chunk.get("choices") or [{}])[0]
    delta = choice.get("delta", {})
    finish_reason = choice.get("finish_reason")

    text_delta = delta.get("content")
    if text_delta:
        events.append(
            {
                "type": "response.output_text.delta",
                "response_id": f"resp_{chunk_id}",
                "model": model,
                "delta": text_delta,
            }
        )

    if finish_reason:
        events.append(
            {
                "type": "response.completed",
                "response_id": f"resp_{chunk_id}",
                "model": model,
                "finish_reason": finish_reason,
            }
        )

    return events


def openai_finish_reason_to_anthropic(stop_reason: str | None) -> str:
    if stop_reason == "tool_calls":
        return "tool_use"
    if stop_reason == "length":
        return "max_tokens"
    return "end_turn"


def extract_openai_usage(usage_obj: Any) -> dict[str, int]:
    if not isinstance(usage_obj, dict):
        return {"input_tokens": 0, "output_tokens": 0}

    input_tokens = usage_obj.get("prompt_tokens")
    output_tokens = usage_obj.get("completion_tokens")

    # 兼容不同供应商的字段命名。
    if input_tokens is None:
        input_tokens = usage_obj.get("input_tokens", 0)
    if output_tokens is None:
        output_tokens = usage_obj.get("output_tokens", 0)

    try:
        in_num = int(input_tokens or 0)
    except Exception:
        in_num = 0
    try:
        out_num = int(output_tokens or 0)
    except Exception:
        out_num = 0
    return {"input_tokens": in_num, "output_tokens": out_num}


def merge_anthropic_usage(base_usage: dict[str, int], new_usage: dict[str, int]) -> dict[str, int]:
    return {
        # 不同厂商 chunk 可能给累积值或增量值，这里用 max 防止重复累加。
        "input_tokens": max(base_usage.get("input_tokens", 0), new_usage.get("input_tokens", 0)),
        "output_tokens": max(base_usage.get("output_tokens", 0), new_usage.get("output_tokens", 0)),
    }


def anthropic_messages_to_openai_chat(payload: dict[str, Any]) -> dict[str, Any]:
    out_messages: list[dict[str, Any]] = []
    system_text = payload.get("system")
    if isinstance(system_text, str) and system_text.strip():
        out_messages.append({"role": "system", "content": system_text})

    def _block_text(block_content: Any) -> str:
        if isinstance(block_content, str):
            return block_content
        if isinstance(block_content, list):
            texts: list[str] = []
            for x in block_content:
                if isinstance(x, dict) and x.get("type") == "text":
                    texts.append(str(x.get("text", "")))
            return "".join(texts)
        return str(block_content)

    for msg in payload.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, str):
            out_messages.append({"role": role, "content": content})
            continue
        if isinstance(content, list):
            if role == "assistant":
                text_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                for block in content:
                    block_type = block.get("type")
                    if block_type == "text":
                        text_parts.append(block.get("text", ""))
                    elif block_type == "tool_use":
                        tool_calls.append(
                            {
                                "id": block.get("id", f"call_{uuid.uuid4().hex}"),
                                "type": "function",
                                "function": {
                                    "name": block.get("name", "tool"),
                                    "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                                },
                            }
                        )
                assistant_msg: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                out_messages.append(assistant_msg)
                continue

            # user 消息中兼容 tool_result -> OpenAI tool role
            text_parts: list[str] = []
            tool_results: list[dict[str, Any]] = []
            for block in content:
                block_type = block.get("type")
                if block_type == "text":
                    text_parts.append(block.get("text", ""))
                elif block_type == "tool_result":
                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", f"call_{uuid.uuid4().hex}"),
                            "content": _block_text(block.get("content", "")),
                        }
                    )
            if text_parts:
                out_messages.append({"role": role, "content": "".join(text_parts)})
            out_messages.extend(tool_results)
            continue

    return {
        "model": payload.get("model", "gpt-4o-mini"),
        "messages": out_messages,
        "max_tokens": payload.get("max_tokens", 1024),
        "temperature": payload.get("temperature", 1),
        "stream": bool(payload.get("stream", False)),
    }


def openai_chat_to_anthropic_message(
    chat_resp: dict[str, Any], model: str, finish_reason: str | None = None
) -> dict[str, Any]:
    choice = (chat_resp.get("choices") or [{}])[0]
    msg = choice.get("message", {})
    text = msg.get("content", "")
    tool_calls = msg.get("tool_calls", [])
    usage = chat_resp.get("usage", {})
    content_blocks: list[dict[str, Any]] = []
    if text:
        content_blocks.append({"type": "text", "text": text})

    for call in tool_calls:
        fn = call.get("function", {}) if isinstance(call, dict) else {}
        fn_name = fn.get("name", "tool")
        fn_args_raw = fn.get("arguments", "{}")
        try:
            fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
        except json.JSONDecodeError:
            fn_args = {"raw_arguments": str(fn_args_raw)}
        content_blocks.append(
            {
                "type": "tool_use",
                "id": call.get("id", f"toolu_{uuid.uuid4().hex}"),
                "name": fn_name,
                "input": fn_args if isinstance(fn_args, dict) else {"value": fn_args},
            }
        )

    return {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content_blocks,
        "stop_reason": openai_finish_reason_to_anthropic(
            finish_reason or choice.get("finish_reason") or ("tool_calls" if tool_calls else "stop")
        ),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }

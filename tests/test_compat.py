import unittest

from app.compat import (
    anthropic_content_to_openai_message,
    anthropic_messages_to_openai_chat,
    chat_completion_to_responses,
    extract_openai_usage,
    merge_anthropic_usage,
    openai_finish_reason_to_anthropic,
    openai_chat_to_anthropic_message,
    openai_chat_stream_chunk_to_response_events,
    responses_input_to_messages,
)


class CompatTests(unittest.TestCase):
    def test_responses_input_to_messages_with_str(self) -> None:
        payload = {"input": "hello"}
        messages = responses_input_to_messages(payload)
        self.assertEqual(messages, [{"role": "user", "content": "hello"}])

    def test_chat_completion_to_responses(self) -> None:
        chat_resp = {
            "model": "gpt-4.1-mini",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        out = chat_completion_to_responses(chat_resp)
        self.assertEqual(out["object"], "response")
        self.assertEqual(out["model"], "gpt-4.1-mini")
        self.assertEqual(out["output"][0]["content"][0]["text"], "ok")

    def test_anthropic_content_to_openai_message_with_tool(self) -> None:
        content = [
            {"type": "text", "text": "先查天气。"},
            {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "shenzhen"}},
        ]
        text, tools = anthropic_content_to_openai_message(content)
        self.assertEqual(text, "先查天气。")
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["function"]["name"], "get_weather")

    def test_openai_chat_stream_chunk_to_response_events(self) -> None:
        chunk = {
            "id": "chatcmpl_1",
            "model": "gpt-test",
            "choices": [{"delta": {"content": "Hi"}, "finish_reason": None}],
        }
        events = openai_chat_stream_chunk_to_response_events(chunk)
        self.assertEqual(events[0]["type"], "response.output_text.delta")
        self.assertEqual(events[0]["delta"], "Hi")

    def test_anthropic_messages_to_openai_chat_with_tool_result(self) -> None:
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "正在查询"},
                        {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "shenzhen"}},
                    ],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "晴天"}],
                },
            ],
        }
        out = anthropic_messages_to_openai_chat(payload)
        self.assertEqual(out["messages"][0]["role"], "assistant")
        self.assertEqual(out["messages"][0]["tool_calls"][0]["id"], "toolu_1")
        self.assertEqual(out["messages"][1]["role"], "tool")
        self.assertEqual(out["messages"][1]["tool_call_id"], "toolu_1")

    def test_openai_chat_to_anthropic_message_with_tool_calls(self) -> None:
        chat_resp = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "get_weather", "arguments": "{\"city\":\"shenzhen\"}"},
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3},
        }
        out = openai_chat_to_anthropic_message(chat_resp, "gpt-4o")
        self.assertEqual(out["stop_reason"], "tool_use")
        self.assertEqual(out["content"][0]["type"], "tool_use")
        self.assertEqual(out["content"][0]["name"], "get_weather")

    def test_openai_finish_reason_mapping(self) -> None:
        self.assertEqual(openai_finish_reason_to_anthropic("tool_calls"), "tool_use")
        self.assertEqual(openai_finish_reason_to_anthropic("length"), "max_tokens")
        self.assertEqual(openai_finish_reason_to_anthropic("stop"), "end_turn")

    def test_extract_openai_usage_with_alternative_keys(self) -> None:
        usage = extract_openai_usage({"input_tokens": 12, "output_tokens": 34})
        self.assertEqual(usage["input_tokens"], 12)
        self.assertEqual(usage["output_tokens"], 34)

    def test_merge_anthropic_usage_uses_max(self) -> None:
        merged = merge_anthropic_usage(
            {"input_tokens": 10, "output_tokens": 20},
            {"input_tokens": 8, "output_tokens": 30},
        )
        self.assertEqual(merged["input_tokens"], 10)
        self.assertEqual(merged["output_tokens"], 30)


if __name__ == "__main__":
    unittest.main()

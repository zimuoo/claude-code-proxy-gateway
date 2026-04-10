import time
from typing import Any

import httpx

from app.config import ProviderConfig
from app.http_utils import RETRYABLE_STATUS


def _join_url(base_url: str, api_prefix: str, path: str) -> str:
    prefix = (api_prefix or "/v1").strip()
    if not prefix.startswith("/"):
        prefix = f"/{prefix}"
    if prefix == "/":
        return f"{base_url}/{path.lstrip('/')}"
    return f"{base_url}{prefix}/{path.lstrip('/')}"


def _auth_headers(provider: ProviderConfig) -> dict[str, str]:
    headers = dict(provider.extra_headers)
    if provider.provider_type == "anthropic":
        headers["x-api-key"] = provider.api_key
        headers["anthropic-version"] = provider.anthropic_version
        headers["content-type"] = "application/json"
        return headers

    if provider.api_key:
        if provider.auth_scheme.lower() == "bearer":
            headers[provider.auth_header] = f"Bearer {provider.api_key}"
        else:
            headers[provider.auth_header] = provider.api_key
    return headers


async def probe_provider(provider: ProviderConfig, timeout_seconds: int) -> dict[str, Any]:
    headers = _auth_headers(provider)
    now = int(time.time())
    out: dict[str, Any] = {
        "checked_at": now,
        "online": False,
        "models_endpoint": {"ok": False, "status_code": None},
        "responses_endpoint": {"ok": False, "status_code": None},
        "supports_responses_effective": provider.supports_responses,
        "notes": [],
    }

    if not provider.api_key:
        out["notes"].append("empty api_key; probe result may be invalid")

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            if provider.provider_type == "anthropic":
                models_url = f"{provider.base_url}/v1/models"
            else:
                models_url = _join_url(provider.base_url, provider.api_prefix, "models")

            models_resp = await client.get(models_url, headers=headers)
            out["models_endpoint"]["status_code"] = models_resp.status_code
            out["models_endpoint"]["ok"] = models_resp.status_code < 500

            if provider.provider_type == "anthropic":
                responses_url = f"{provider.base_url}/v1/messages"
                responses_body: dict[str, Any] = {
                    "model": "claude-sonnet-4-20250514",
                    "messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}],
                    "max_tokens": 1,
                }
            else:
                responses_url = _join_url(provider.base_url, provider.api_prefix, "responses")
                responses_body = {"model": "probe-model", "input": "ping", "max_output_tokens": 1}

            responses_resp = await client.post(responses_url, headers=headers, json=responses_body)
            out["responses_endpoint"]["status_code"] = responses_resp.status_code

            # 404/405/501 一般表示端点不存在；其他状态可能是模型错误或权限问题，视为“端点存在”。
            responses_exists = responses_resp.status_code not in {404, 405, 501}
            out["responses_endpoint"]["ok"] = responses_exists
            out["supports_responses_effective"] = provider.supports_responses and responses_exists

            if responses_resp.status_code in RETRYABLE_STATUS:
                out["notes"].append("responses endpoint returns retryable status; check provider stability")

            out["online"] = out["models_endpoint"]["ok"] or out["responses_endpoint"]["ok"]
            return out
    except Exception as exc:
        out["notes"].append(f"probe_error: {str(exc)}")
        return out

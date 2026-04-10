import asyncio
from typing import Any

import httpx


RETRYABLE_STATUS = {429, 500, 502, 503, 504}


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    content: bytes | None = None,
    json_payload: dict[str, Any] | None = None,
    max_attempts: int = 3,
    backoff_ms: int = 300,
) -> httpx.Response:
    attempt = 0
    last_exc: Exception | None = None
    while attempt < max_attempts:
        attempt += 1
        try:
            resp = await client.request(method, url, headers=headers, content=content, json=json_payload)
            if resp.status_code not in RETRYABLE_STATUS or attempt >= max_attempts:
                return resp
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt >= max_attempts:
                raise
        await asyncio.sleep((backoff_ms / 1000.0) * attempt)
    if last_exc:
        raise last_exc
    raise RuntimeError("request_with_retry failed without response")

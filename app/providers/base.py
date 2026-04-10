from abc import ABC, abstractmethod
from typing import Any

from fastapi import Request
from starlette.responses import Response

from app.config import ProviderConfig


class ProviderAdapter(ABC):
    def __init__(
        self,
        config: ProviderConfig,
        timeout_seconds: int,
        retry_max_attempts: int,
        retry_backoff_ms: int,
    ) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.retry_max_attempts = retry_max_attempts
        self.retry_backoff_ms = retry_backoff_ms

    @abstractmethod
    async def handle(self, request: Request, path: str, payload: dict[str, Any] | None) -> Response:
        raise NotImplementedError

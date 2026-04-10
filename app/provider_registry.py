from fnmatch import fnmatch
import asyncio
import time
from typing import Any

from fastapi import HTTPException

from app.config import AppConfig
from app.probe import probe_provider
from app.providers.anthropic import AnthropicAdapter
from app.providers.base import ProviderAdapter
from app.providers.openai_compatible import OpenAICompatibleAdapter


class ProviderRegistry:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.adapters: dict[str, ProviderAdapter] = {}
        self.capabilities_cache: dict[str, dict[str, Any]] = {}
        self.capabilities_checked_at: int | None = None
        for name, provider in config.providers.items():
            if provider.provider_type == "anthropic":
                self.adapters[name] = AnthropicAdapter(
                    provider,
                    config.timeout_seconds,
                    config.retry_max_attempts,
                    config.retry_backoff_ms,
                )
            else:
                self.adapters[name] = OpenAICompatibleAdapter(
                    provider,
                    config.timeout_seconds,
                    config.retry_max_attempts,
                    config.retry_backoff_ms,
                )

    async def probe_capabilities(self, provider_names: list[str] | None = None) -> dict[str, Any]:
        names = provider_names or list(self.config.providers.keys())
        selected_names = [n for n in names if n in self.config.providers]
        tasks = [
            probe_provider(self.config.providers[name], self.config.probe_timeout_seconds)
            for name in selected_names
        ]
        results = await asyncio.gather(*tasks)
        for name, result in zip(selected_names, results):
            self.capabilities_cache[name] = result
        self.capabilities_checked_at = int(time.time())
        return {
            "checked_at": self.capabilities_checked_at,
            "providers": {name: self.capabilities_cache.get(name, {}) for name in selected_names},
        }

    def get_capabilities(self) -> dict[str, Any]:
        return {
            "checked_at": self.capabilities_checked_at,
            "providers": self.capabilities_cache,
        }

    def _provider_by_model(self, model: str | None) -> str | None:
        if not model:
            return None
        for pattern, provider_name in self.config.model_provider_map.items():
            if fnmatch(model, pattern):
                return provider_name
        return None

    def _fallback_chain_for_model(self, model: str | None) -> list[str]:
        if not model:
            return []
        for pattern, providers in self.config.model_fallbacks.items():
            if fnmatch(model, pattern):
                seen: set[str] = set()
                out: list[str] = []
                for name in providers:
                    if name in self.config.providers and name not in seen:
                        out.append(name)
                        seen.add(name)
                return out
        return []

    def _supports_path(self, provider_name: str, path: str) -> bool:
        provider = self.config.providers[provider_name]
        caps = self.capabilities_cache.get(provider_name, {})
        if path == "responses":
            if caps:
                return bool(caps.get("supports_responses_effective", provider.supports_responses))
            return provider.supports_responses
        return True

    def _is_online(self, provider_name: str) -> bool:
        caps = self.capabilities_cache.get(provider_name)
        if not caps:
            return True
        return bool(caps.get("online", False))

    def _fallback_candidates(self, path: str) -> list[str]:
        names = list(self.config.providers.keys())
        ordered: list[str] = []

        # 优先 default provider。
        if self.config.default_provider in names:
            ordered.append(self.config.default_provider)

        # 优先常见可兜底 provider。
        for preferred in ("openai", "openrouter"):
            if preferred in names and preferred not in ordered:
                ordered.append(preferred)

        for name in names:
            if name not in ordered:
                ordered.append(name)

        return [name for name in ordered if self._supports_path(name, path) and self._is_online(name)]

    def pick_provider_name(
        self,
        path: str,
        payload: dict[str, Any] | None,
        x_proxy_provider: str | None,
    ) -> str:
        if x_proxy_provider:
            if x_proxy_provider not in self.adapters:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown provider in x-proxy-provider: {x_proxy_provider}",
                )
            if not self._supports_path(x_proxy_provider, path):
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{x_proxy_provider}' does not support /v1/{path}",
                )
            return x_proxy_provider

        provider_name_in_body = (payload or {}).get("provider")
        if provider_name_in_body:
            if provider_name_in_body not in self.adapters:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown provider in request body provider field: {provider_name_in_body}",
                )
            if not self._supports_path(provider_name_in_body, path):
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{provider_name_in_body}' does not support /v1/{path}",
                )
            return provider_name_in_body

        model = (payload or {}).get("model")
        provider_by_model = self._provider_by_model(model)
        candidates: list[str] = []
        if provider_by_model and provider_by_model in self.adapters:
            candidates.append(provider_by_model)
        candidates.extend(self._fallback_chain_for_model(model))

        seen_candidates: set[str] = set()
        for candidate in candidates:
            if candidate in seen_candidates:
                continue
            seen_candidates.add(candidate)
            if self._supports_path(candidate, path) and self._is_online(candidate):
                return candidate

        if self._supports_path(self.config.default_provider, path) and self._is_online(
            self.config.default_provider
        ):
            return self.config.default_provider

        fallbacks = self._fallback_candidates(path)
        if fallbacks:
            return fallbacks[0]

        raise HTTPException(
            status_code=503,
            detail=f"No available provider for /v1/{path}. Run /proxy/providers/probe and check capabilities.",
        )

    def get_adapter(self, provider_name: str) -> ProviderAdapter:
        return self.adapters[provider_name]

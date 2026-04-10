import unittest

from app.config import AppConfig, ProviderConfig
from app.provider_registry import ProviderRegistry


class ProviderRegistryTests(unittest.TestCase):
    def _build_registry(self) -> ProviderRegistry:
        providers = {
            "openai": ProviderConfig(
                name="openai",
                provider_type="openai_compatible",
                base_url="https://api.openai.com",
                api_prefix="/v1",
                api_key="k1",
                supports_responses=True,
            ),
            "deepseek": ProviderConfig(
                name="deepseek",
                provider_type="openai_compatible",
                base_url="https://api.deepseek.com",
                api_prefix="/v1",
                api_key="k2",
                supports_responses=False,
            ),
        }
        cfg = AppConfig(
            port=8080,
            timeout_seconds=180,
            default_provider="deepseek",
            providers=providers,
            model_provider_map={"gpt-*": "openai", "deepseek-*": "deepseek"},
            model_fallbacks={"deepseek-*": ["openai"]},
            gateway_api_keys=[],
            retry_max_attempts=3,
            retry_backoff_ms=300,
            probe_on_startup=False,
            probe_timeout_seconds=8,
        )
        return ProviderRegistry(cfg)

    def test_responses_fallback_to_supporting_provider(self) -> None:
        registry = self._build_registry()
        provider = registry.pick_provider_name(
            "responses",
            {"model": "deepseek-chat"},
            x_proxy_provider=None,
        )
        self.assertEqual(provider, "openai")

    def test_model_mapping_when_supported(self) -> None:
        registry = self._build_registry()
        provider = registry.pick_provider_name(
            "chat/completions",
            {"model": "deepseek-chat"},
            x_proxy_provider=None,
        )
        self.assertEqual(provider, "deepseek")

    def test_probe_cache_online_fallback(self) -> None:
        registry = self._build_registry()
        registry.capabilities_cache = {
            "deepseek": {"online": False, "supports_responses_effective": False},
            "openai": {"online": True, "supports_responses_effective": True},
        }
        provider = registry.pick_provider_name(
            "chat/completions",
            {"model": "deepseek-chat"},
            x_proxy_provider=None,
        )
        self.assertEqual(provider, "openai")

    def test_model_specific_fallback_chain_first(self) -> None:
        registry = self._build_registry()
        registry.capabilities_cache = {
            "deepseek": {"online": False, "supports_responses_effective": False},
            "openai": {"online": True, "supports_responses_effective": True},
        }
        provider = registry.pick_provider_name(
            "chat/completions",
            {"model": "deepseek-chat"},
            x_proxy_provider=None,
        )
        self.assertEqual(provider, "openai")


if __name__ == "__main__":
    unittest.main()

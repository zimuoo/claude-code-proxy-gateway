import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


@dataclass
class ProviderConfig:
    name: str
    provider_type: str
    base_url: str
    api_prefix: str = "/v1"
    api_key: str = ""
    auth_scheme: str = "bearer"
    auth_header: str = "Authorization"
    extra_headers: dict[str, str] = field(default_factory=dict)
    anthropic_version: str = "2023-06-01"
    supports_responses: bool = True


@dataclass
class AppConfig:
    port: int
    timeout_seconds: int
    default_provider: str
    providers: dict[str, ProviderConfig]
    model_provider_map: dict[str, str]
    model_fallbacks: dict[str, list[str]]
    gateway_api_keys: list[str]
    retry_max_attempts: int
    retry_backoff_ms: int
    probe_on_startup: bool
    probe_timeout_seconds: int


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency 'PyYAML'. Please install requirements.txt before starting the proxy."
        ) from exc

    if not path.exists():
        raise FileNotFoundError(
            f"providers config not found: {path}. Copy config/providers.example.yaml to this path first."
        )

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return _expand_env(raw)


def load_config() -> AppConfig:
    providers_config_path = os.environ.get("PROVIDERS_CONFIG_PATH", "./config/providers.yaml")
    config_data = _load_yaml(Path(providers_config_path))

    providers_raw = config_data.get("providers", {})
    providers: dict[str, ProviderConfig] = {}
    for name, cfg in providers_raw.items():
        providers[name] = ProviderConfig(
            name=name,
            provider_type=cfg.get("type", "openai_compatible"),
            base_url=cfg.get("base_url", "").rstrip("/"),
            api_prefix=str(cfg.get("api_prefix", "/v1") or "/v1"),
            api_key=cfg.get("api_key", ""),
            auth_scheme=cfg.get("auth_scheme", "bearer"),
            auth_header=cfg.get("auth_header", "Authorization"),
            extra_headers=cfg.get("extra_headers", {}),
            anthropic_version=cfg.get("anthropic_version", "2023-06-01"),
            supports_responses=cfg.get("supports_responses", True),
        )

    if not providers:
        raise ValueError("No providers configured in providers.yaml")

    default_provider = os.environ.get(
        "DEFAULT_PROVIDER",
        config_data.get("default_provider", next(iter(providers.keys()))),
    )
    if default_provider not in providers:
        raise ValueError(f"default_provider '{default_provider}' not found in providers config")

    gateway_api_keys = [
        key.strip()
        for key in os.environ.get("GATEWAY_API_KEYS", "").split(",")
        if key.strip()
    ]

    probe_on_startup = os.environ.get("PROBE_ON_STARTUP", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    return AppConfig(
        port=int(os.environ.get("PORT", "8080")),
        timeout_seconds=int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "180")),
        default_provider=default_provider,
        providers=providers,
        model_provider_map=config_data.get("model_provider_map", {}),
        model_fallbacks=config_data.get("model_fallbacks", {}),
        gateway_api_keys=gateway_api_keys,
        retry_max_attempts=int(os.environ.get("RETRY_MAX_ATTEMPTS", "3")),
        retry_backoff_ms=int(os.environ.get("RETRY_BACKOFF_MS", "300")),
        probe_on_startup=probe_on_startup,
        probe_timeout_seconds=int(os.environ.get("PROBE_TIMEOUT_SECONDS", "8")),
    )

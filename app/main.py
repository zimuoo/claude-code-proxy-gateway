from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import load_config
from app.provider_registry import ProviderRegistry


app = FastAPI(title="OpenAI Multi-Provider Proxy", version="0.1.0")
config = load_config()
registry = ProviderRegistry(config)


@app.on_event("startup")
async def on_startup() -> None:
    if config.probe_on_startup:
        await registry.probe_capabilities()


def _check_gateway_auth(request: Request) -> None:
    if not config.gateway_api_keys:
        return
    auth_header = request.headers.get("authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header else ""
    if token not in config.gateway_api_keys:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "default_provider": config.default_provider,
        "providers": sorted(config.providers.keys()),
    }


@app.get("/proxy/providers")
async def list_providers(request: Request) -> dict[str, Any]:
    _check_gateway_auth(request)
    return {
        "default_provider": config.default_provider,
        "providers": {
            name: {
                "type": p.provider_type,
                "base_url": p.base_url,
                "api_prefix": p.api_prefix,
                "supports_responses": p.supports_responses,
            }
            for name, p in config.providers.items()
        },
        "model_provider_map": config.model_provider_map,
        "model_fallbacks": config.model_fallbacks,
    }


@app.get("/proxy/providers/capabilities")
async def providers_capabilities(request: Request) -> dict[str, Any]:
    _check_gateway_auth(request)
    return registry.get_capabilities()


@app.post("/proxy/providers/probe")
async def probe_providers(request: Request, provider: str | None = None) -> dict[str, Any]:
    _check_gateway_auth(request)
    if provider:
        if provider not in config.providers:
            raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
        return await registry.probe_capabilities([provider])
    return await registry.probe_capabilities()


@app.api_route(
    "/v1/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy_v1(path: str, request: Request, x_proxy_provider: str | None = Header(default=None)) -> Any:
    _check_gateway_auth(request)

    payload: dict[str, Any] | None = None
    if request.method in {"POST", "PUT", "PATCH"}:
        try:
            payload = await request.json()
        except Exception:
            payload = None

    provider_name = registry.pick_provider_name(path, payload, x_proxy_provider)
    adapter = registry.get_adapter(provider_name)

    try:
        return await adapter.handle(request, path, payload)
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "message": f"proxy request failed: {str(exc)}",
                    "type": "proxy_error",
                    "provider": provider_name,
                }
            },
        )

# OpenAI / Anthropic Multi-Provider Proxy

This project is a **unified model proxy gateway** designed to:

- Provide one endpoint for **OpenAI-compatible clients**, with model-based routing across providers.
- Provide an **Anthropic-style endpoint** (`/v1/messages`) for Claude Code.
- Centralize API keys, routing rules, retries, and provider capability probing.

---

## Why this project

In real-world usage, teams often use multiple vendors at the same time (OpenAI, DeepSeek, Qwen, Moonshot, Mistral, Groq, Anthropic, etc.).  
Each vendor has slight differences in base URLs, auth patterns, and API capabilities.

This gateway unifies those differences:

- One external Base URL: `http://127.0.0.1:8080`
- Internal multi-provider config: `config/providers.yaml`
- Auto/manual routing with fallback
- Both OpenAI-style and Anthropic-style inbound support

---

## Core capabilities

- **OpenAI-style ingress**: `/v1/*`
  - Common endpoints like `chat/completions`, `responses`, `models`, etc.
- **Anthropic-style ingress**: `/v1/messages`, `/v1/models`
  - Compatible with Claude Code workflows.
- **Multi-provider routing**
  - Auto route by `model_provider_map`
  - Model-level fallback chain via `model_fallbacks`
  - Force provider by `x-proxy-provider` header
- **Compatibility transforms**
  - Auto downgrade `responses` to `chat/completions` when provider does not support `responses` (including streaming).
  - Basic Anthropic `tool_use` -> OpenAI `tool_calls` mapping.
  - Basic Anthropic `/v1/messages` -> OpenAI `chat/completions` mapping.
  - Basic bidirectional `tool_use/tool_result` compatibility for Claude `messages` flow.
- **Reliability**
  - Retry for 429/5xx (configurable)
  - Provider capability cache (`online`, `/models`, `/responses`)
  - Routing decisions can use probe results for fallback

---

## Supported providers and model families

`config/providers.example.yaml` includes a broad template:

- Global: OpenAI, Anthropic, Gemini(OpenAI-compatible), Groq, Together, Mistral, Perplexity, xAI, Fireworks, Cerebras, SambaNova, NVIDIA NIM
- China: DeepSeek, Qwen, Moonshot, Zhipu, Baichuan, MiniMax, Yi, Hunyuan, SiliconFlow, Volcengine Ark, Tencent LKEAP
- Aggregators: OpenRouter, OneAPI, NewAPI

> Note:  
> “All models on the market” changes constantly.  
> The project is designed to be extensible via configurable providers, model mappings, and fallback chains.

---

## Quick deployment

### 1) Local run

```bash
cd claude-code-proxy-gateway
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/providers.example.yaml config/providers.yaml
cp .env.example .env
python run.py
```

Default bind: `0.0.0.0:8080`

### 2) Docker run

```bash
cd claude-code-proxy-gateway
docker build -t claude-code-proxy-gateway:latest .
docker run --rm -p 8080:8080 \
  -e PROVIDERS_CONFIG_PATH=/app/config/providers.yaml \
  -e OPENAI_API_KEY=xxx \
  -e ANTHROPIC_API_KEY=xxx \
  claude-code-proxy-gateway:latest
```

---

## Configuration

### `config/providers.yaml`

Copy from `config/providers.example.yaml`, then define:

- `default_provider`
- `providers` (`base_url`, `api_prefix`, `api_key`, `supports_responses`, etc.)
- `model_provider_map` (model pattern -> provider)
- `model_fallbacks` (model pattern -> ordered fallback providers)

### Environment variables (gateway process)

- `PORT` (default `8080`)
- `PROVIDERS_CONFIG_PATH`
- `GATEWAY_API_KEYS` (comma-separated tokens)
- `RETRY_MAX_ATTEMPTS` (default `3`)
- `RETRY_BACKOFF_MS` (default `300`)
- `PROBE_ON_STARTUP` (default `false`)
- `PROBE_TIMEOUT_SECONDS` (default `8`)

---

## Usage (OpenAI-style clients)

### Auto routing

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer token1" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role":"user","content":"hello"}]
  }'
```

### Force provider

```bash
curl http://127.0.0.1:8080/v1/responses \
  -H "Authorization: Bearer token1" \
  -H "x-proxy-provider: openai" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4.1-mini",
    "input": "write a haiku"
  }'
```

---

## Claude Code setup (important)

Set these variables in `~/.claude/settings.json` (or `~/.claude/setting.json` in some versions):

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8080",
    "ANTHROPIC_AUTH_TOKEN": "token1",
    "ANTHROPIC_MODEL": "gpt-4o",
    "ANTHROPIC_SMALL_FAST_MODEL": "gpt-4o-mini"
  }
}
```

Notes:

- `ANTHROPIC_BASE_URL` points to this proxy root (Claude will call `/v1/messages`).
- `ANTHROPIC_AUTH_TOKEN` must match one token in `GATEWAY_API_KEYS`.
- `ANTHROPIC_MODEL` / `ANTHROPIC_SMALL_FAST_MODEL` are routed by `model_provider_map`.
- If configured, `model_fallbacks` is used when the primary provider is unavailable.

---

## Admin endpoints

> If `GATEWAY_API_KEYS` is set, these endpoints also require `Authorization: Bearer <token>`

### Provider summary

```bash
curl -H "Authorization: Bearer token1" \
  http://127.0.0.1:8080/proxy/providers
```

### Trigger probing

```bash
curl -X POST -H "Authorization: Bearer token1" \
  http://127.0.0.1:8080/proxy/providers/probe
```

### Read probe cache

```bash
curl -H "Authorization: Bearer token1" \
  http://127.0.0.1:8080/proxy/providers/capabilities
```

---

## Tests

```bash
cd claude-code-proxy-gateway
python -m unittest discover -s tests -p "test_*.py"
```

---

## Current compatibility boundaries

- Common OpenAI/Anthropic conversation flows are supported.
- Vendor-specific details for tools, image, audio, and Responses events still vary.
- For production, add rate limiting, audit logs, tenant isolation, circuit breakers, and alerting.

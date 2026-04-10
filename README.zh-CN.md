# OpenAI / Anthropic 多 Provider 代理网关

这个项目是一个 **多模型统一代理网关**，核心用途是：

- 给 **OpenAI 协议客户端** 提供统一入口，按模型名自动路由到不同厂商。
- 给 **Claude Code（Anthropic 协议）** 提供可配置代理入口（`/v1/messages`）。
- 在一个地址下统一管理多家大模型 API Key、路由规则、重试策略和可用性探测。

---

## 这个项目解决什么问题

在真实开发里，通常会同时使用 OpenAI、DeepSeek、Qwen、Moonshot、Mistral、Groq、Anthropic 等多家模型。  
每家 API 地址、认证方式、能力支持都略有差异，切换成本高。

本项目把这些差异收敛到一个网关中：

- 对外一个 Base URL：`http://127.0.0.1:8080`
- 对内多 Provider 配置：`config/providers.yaml`
- 支持自动/手动路由 + 失败回退
- 支持 OpenAI 协议与 Anthropic 协议入站

---

## 主要能力

- **OpenAI 协议入口**：`/v1/*`
  - 常见端点如 `chat/completions`、`responses`、`models` 等。
- **Anthropic 协议入口**：`/v1/messages`、`/v1/models`
  - 适配 Claude Code 使用。
- **多 Provider 路由**
  - 按 `model_provider_map` 自动路由。
  - 支持 `model_fallbacks` 模型级回退链（主路由失败后按顺序回退）。
  - 请求头 `x-proxy-provider` 强制指定 provider。
- **兼容转换**
  - `responses` 不支持时自动降级到 `chat/completions`（含流式）。
  - Anthropic `tool_use` 到 OpenAI `tool_calls` 的基础映射。
  - Anthropic `/v1/messages` 到 OpenAI `chat/completions` 的基础转换（用于 Claude Code 接 OpenAI 兼容模型）。
  - Claude `messages` 链路支持 `tool_use/tool_result` 的基础双向兼容（非流式完整、流式含 `message_delta/usage` 基础映射）。
- **稳定性**
  - 429/5xx 自动重试（可配置）。
  - Provider 探测缓存（在线状态、`/models`、`/responses`）。
  - 路由会参考探测结果，自动回退可用 provider。

---

## 支持的模型与厂商

`config/providers.example.yaml` 已给出主流模板，包含：

- 国际：OpenAI、Anthropic、Gemini(OpenAI兼容)、Groq、Together、Mistral、Perplexity、xAI、Fireworks、Cerebras、SambaNova、NVIDIA NIM
- 国内：DeepSeek、Qwen、Moonshot、智谱、百川、MiniMax、零一万物、腾讯混元、SiliconFlow、火山方舟、腾讯 LKEAP
- 聚合：OpenRouter、OneAPI、NewAPI

> 说明：  
> “市面上所有模型”会持续变化，项目采用 **可配置 Provider + 可配置模型映射** 机制来长期扩展。  
> 你只需要在 `providers.yaml` 新增 provider、模型前缀映射和可选回退链即可。

---

## 快速部署

### 1) 本地运行

```bash
cd claude-code-proxy-gateway
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/providers.example.yaml config/providers.yaml
cp .env.example .env
python run.py
```

默认监听：`0.0.0.0:8080`

### 2) Docker 运行

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

## 关键配置文件

### `config/providers.yaml`

从 `config/providers.example.yaml` 复制而来，用于定义：

- `default_provider`
- `providers`（每个厂商的 `base_url`、`api_prefix`、`api_key`、`supports_responses` 等）
- `model_provider_map`（模型前缀到 provider 的映射）
- `model_fallbacks`（模型前缀到 fallback provider 列表的映射）

### 环境变量（网关进程）

- `PORT`：监听端口，默认 `8080`
- `PROVIDERS_CONFIG_PATH`：provider 配置文件路径
- `GATEWAY_API_KEYS`：网关鉴权 token 列表（逗号分隔）
- `RETRY_MAX_ATTEMPTS`：重试次数，默认 `3`
- `RETRY_BACKOFF_MS`：退避毫秒，默认 `300`
- `PROBE_ON_STARTUP`：启动时是否自动探测 provider，默认 `false`
- `PROBE_TIMEOUT_SECONDS`：探测超时秒数，默认 `8`

---

## 如何使用（OpenAI 客户端）

### 自动路由

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer token1" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role":"user","content":"hello"}]
  }'
```

### 强制指定 Provider

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

## 如何在 Claude Code 中配置这个代理（重点）

这组变量需要配置：`ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_MODEL`、`ANTHROPIC_SMALL_FAST_MODEL`。  
本项目支持 `Claude Code -> /v1/messages -> Proxy -> 多 Provider` 的链路。

### 1) 先启动代理

确保代理运行在：`http://127.0.0.1:8080`

### 2) 配置 Claude 环境变量

通常在 `~/.claude/settings.json`（部分版本可能是 `~/.claude/setting.json`）中配置：

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

说明：

- `ANTHROPIC_BASE_URL` 指向本代理根地址（Claude 会请求 `/v1/messages`）。
- `ANTHROPIC_AUTH_TOKEN` 对应网关 `GATEWAY_API_KEYS` 中的一个 token。
- `ANTHROPIC_MODEL` / `ANTHROPIC_SMALL_FAST_MODEL` 填目标模型名，实际去向由 `model_provider_map` 决定。
- 若配置了 `model_fallbacks`，主 provider 不可用时会按回退链自动切换。

---

## 管理与运维接口

> 若设置了 `GATEWAY_API_KEYS`，以下接口也需要 `Authorization: Bearer <token>`

### 查看 provider 配置摘要

```bash
curl -H "Authorization: Bearer token1" \
  http://127.0.0.1:8080/proxy/providers
```

### 触发能力探测

```bash
curl -X POST -H "Authorization: Bearer token1" \
  http://127.0.0.1:8080/proxy/providers/probe
```

### 查看能力缓存

```bash
curl -H "Authorization: Bearer token1" \
  http://127.0.0.1:8080/proxy/providers/capabilities
```

---

## 测试

```bash
cd claude-code-proxy-gateway
python -m unittest discover -s tests -p "test_*.py"
```

---

## 当前兼容边界（务必阅读）

- 已支持 OpenAI/Anthropic 常见对话链路和基础流式映射。
- 不同厂商的函数调用、图像、音频、Responses 事件细节仍存在差异，建议按真实场景逐步补齐。
- 生产场景建议补充：限流、审计日志、多租户隔离、熔断与告警。

# OpenAI / Anthropic Multi-Provider Proxy

[中文文档 (README.zh-CN.md)](./README.zh-CN.md) | [English Documentation (README.en.md)](./README.en.md)

Unified proxy gateway for OpenAI-compatible APIs and Anthropic-style clients (including Claude Code).

## Quick Start

```bash
cd claude-code-proxy-gateway
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/providers.example.yaml config/providers.yaml
python run.py
```

## Claude Code Configuration

In `~/.claude/settings.json` (or `~/.claude/setting.json` in some versions):

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

For full setup, provider matrix, deployment options, and routing details, please read:

- [README.zh-CN.md](./README.zh-CN.md)
- [README.en.md](./README.en.md)

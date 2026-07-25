# AgentOS

Multi-agent collaboration OS that orchestrates external AI agents (OpenClaw, Codex, Claude, Gemini, ...) and built-in sub-agents into a single coherent team.

> Agent is the employee, Orchestrator is the management system, Memory is the enterprise knowledge base, Communication Bus is the internal collaboration network.

## Status

Early stage - MVP skeleton (v0.1 protocol frozen). See:
- [docs/01-protocol-v0.1.md](docs/01-protocol-v0.1.md) — frozen decision summary
- [docs/ADR/](docs/ADR/) — Architecture Decision Records
- [docs/02-bootstrap.md](docs/02-bootstrap.md) — setup + GitHub push guide
- [docs/AgentOS_Multi_Agent_Architecture_Design.md](docs/AgentOS_Multi_Agent_Architecture_Design.md) — original ChatGPT-generated design doc

## Architecture

```
User
  -> Web / API / CLI
  -> AgentOS Control Plane (Planner + Orchestrator Engine + Agent Manager + Memory Manager)
  -> A2A Communication Bus
  -> External Agents (OpenClaw / Codex / Claude / Gemini / ...)
  -> Tools Layer (Browser / Terminal / Git / Cloud / API)
```

## Driver Matrix

| Agent | Driver | Protocol |
|---|---|---|
| OpenClaw (chat) | `OpenAIDriver` (via `OpenClawDriver` subclass) | OpenAI-compatible HTTP (`/v1/chat/completions`) |
| OpenClaw (node capabilities) | `WSDriver` | Native WebSocket gateway |
| Codex | `OpenAIDriver` (via Orchestrator-embedded adapter, MVP) | OpenAI-compatible HTTP |
| Claude | `OpenAIDriver` (direct official endpoint) | OpenAI-compatible HTTP |
| Gemini | `OpenAIDriver` (direct official endpoint) | OpenAI-compatible HTTP |

## Layout

- `src/agentos/core/` — Orchestrator Engine
- `src/agentos/planning/` — Task Planner / DAG generator
- `src/agentos/agents/` — Agent Manager / registry
- `src/agentos/drivers/` — Per-agent adapters
  - `base.py` — shared abstract base + `ChatResult` + `DriverError`
  - `openai_driver.py` — OpenAI-compatible chat driver (covers most agents)
  - `ws_driver.py` — WebSocket driver for OpenClaw native gateway
  - `openclaw_driver.py` *(next)* — OpenClaw-specific subclass + memory + config
- `src/agentos/bus/` — A2A Communication Bus
- `src/agentos/memory/` — Cross-agent Memory Service (per ADR-0005)
- `src/agentos/schemas/` — Pydantic models (Artifact, Message, A2A protocol)
- `src/agentos/api/` — FastAPI routes + WebSocket events
- `tests/` — pytest suite
- `docs/ADR/` — Architecture Decision Records (8 ADRs in v0.1)

## Quick Start

```bash
pip install -e .[dev]
pytest
```

Full setup including GitHub push pitfalls: see [docs/02-bootstrap.md](docs/02-bootstrap.md).

## License

MIT
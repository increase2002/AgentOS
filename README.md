# AgentOS

Multi-agent collaboration OS that orchestrates external AI agents (OpenClaw, Codex, Claude, Gemini, ...) and built-in sub-agents into a single coherent team.

> Agent 是员工，Orchestrator 是管理系统，Memory 是企业知识库，Communication Bus 是内部协作网络。

## Status

Early stage - MVP skeleton only. See [docs/AgentOS_Multi_Agent_Architecture_Design.md](docs/AgentOS_Multi_Agent_Architecture_Design.md) for the full design.

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
| OpenClaw (chat) | `OpenAIDriver` | OpenAI-compatible HTTP (`/v1/chat/completions`) |
| OpenClaw (node capabilities) | `WSDriver` | Native WebSocket gateway |
| Codex | `OpenAIDriver` (via Orchestrator-embedded adapter) | OpenAI-compatible HTTP |
| Claude | `OpenAIDriver` (via proxy) | OpenAI-compatible HTTP |
| Gemini | `OpenAIDriver` (via proxy) | OpenAI-compatible HTTP |

## Layout

- `src/agentos/core/` - Orchestrator Engine
- `src/agentos/planning/` - Task Planner / DAG generator
- `src/agentos/agents/` - Agent Manager / registry
- `src/agentos/drivers/` - Per-agent adapters (`BaseDriver`, `OpenAIDriver`, `WSDriver`, ...)
- `src/agentos/bus/` - A2A Communication Bus
- `src/agentos/memory/` - Cross-agent Memory Service
- `src/agentos/schemas/` - Pydantic models (Artifact, Message, A2A protocol)
- `src/agentos/api/` - FastAPI routes + WebSocket events
- `tests/` - pytest suite
- `docs/` - Design documents

## Quick Start (planned)

```bash
pip install -e .[dev]
uvicorn agentos.api.main:app --reload
pytest
```

## License

MIT
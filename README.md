# AgentOS

Multi-agent collaboration OS that orchestrates external AI agents (OpenClaw, Codex, Claude, Gemini, ...) and built-in sub-agents into a single coherent team.

> Agent is the employee, Orchestrator is the management system, Memory is the enterprise knowledge base, Communication Bus is the internal collaboration network.

## Status

**v0.1 shipped.** Protocol frozen (8 ADRs + 0010 + 0011 = 10 decisions). All four vendor drivers (OpenClaw / Codex / Claude / Gemini) work. Orchestrator Engine runs end-to-end (TASK_REQUEST on the bus -> DAG execute -> reply back). JSONL bus + agentos CLI dogfood loop validated.

- **~150 tests passing** across 14 test modules
- **~4000 LOC** across `src/agentos/`
- **2 demos**: `examples/demo_bus_loop.py` (Plan B full loop), `examples/demo_dogfood.py` (pure Engine 4-stage DAG + partial-success replay)

## Architecture

```
User
  -> Web / API / CLI
  -> AgentOS Control Plane
     - Planner (LLM-driven, v0.2)
     - Orchestrator Engine (DAG executor)
     - Agent Manager / registry
     - Memory Service (cross-agent federation)
  -> A2A Communication Bus (JSONL append-only log)
  -> External Agents (OpenClaw / Codex / Claude / Gemini / ...)
  -> Tools Layer (Browser / Terminal / Git / Cloud / API)
```

Data flow:
1. **TASK_REQUEST** arrives on the bus (`G:/AgentOS/.agentos/bus.jsonl`).
2. **Engine** picks it up, calls **Planner** to produce a `TaskDAG`.
3. **DAGRunner** walks the DAG (topological + parallel_group), invoking each stage via the right **Driver**.
4. **JSONLHook** records every driver call + bus event + stage transition to `G:/AgentOS/telemetry/{date}.jsonl`.
5. **CheckpointStore** persists per-stage results so partial-success replays skip completed stages.
6. **TASK_PROGRESS** + final **TASK_ACCEPT** go back on the bus.

## Driver Matrix

| Agent | Driver | Protocol |
|---|---|---|
| **OpenClaw** (chat) | `OpenClawDriver` (subclass of `OpenAIDriver`) | OpenAI-compatible HTTP Contract B (`/v1/chat/completions`); also has Contract A native WS gateway via `WSDriver` |
| **Codex** | `CodexAdapter` | FastAPI wrapping Codex CLI subprocess (config-driven invocation) |
| **Anthropic (Claude)** | `AnthropicDriver` | Anthropic Messages API native, format-converted at boundary |
| **Google Gemini** | `GeminiDriver` | OpenAI-compatible endpoint (`/v1beta/openai/`) |
| **Shared base** | `OpenAIDriver` | Reusable OpenAI-compatible chat driver (covers most agents) |

All drivers expose `async chat(brief, attachments=None, session_key=None, tool_subset=None) -> ChatResult`. `tool_subset` enforces plan-only / read-only via system-prompt injection (ADR-0009 MVP).

## Layout

```
src/agentos/
  orchestrator/
    engine.py            # Engine class: TASK_REQUEST -> run() -> reply
    dag_runner.py        # Topological + parallel_group, semaphore(4), retry
    checkpoint.py        # TaskCheckpointStore (per-task JSON, atomic write)
    bus_loop.py          # BusWatcher integration + auto-reply
    session_keys.py      # task:<id>:stage:<id> builder/validator
  memory/
    base.py              # BaseMemoryDriver + MemoryHit + MemorySearchResult
    service.py           # MemoryService: fan-out + normalize + rerank
    rerank.py            # CrossEncoderReranker + GPT4oMiniReranker + NullReranker
    empty_drivers.py     # Codex/Claude/Gemini Empty-tier (per ADR-0011)
  drivers/
    base.py              # BaseDriver abstract + ChatResult + DriverError
    openai_driver.py     # OpenAI-compatible driver (tool_subset enforcement)
    ws_driver.py         # WebSocket driver for OpenClaw native gateway
    openclaw_driver.py   # OpenClaw-specific subclass
    openclaw_memory.py   # OpenClaw memory_search driver (MVP stub)
    openclaw_config.py   # Pydantic + JSON5 loader for openclaw.json
    codex_adapter.py     # Codex CLI subprocess wrapper
    anthropic_driver.py  # Anthropic Messages API native driver
    gemini_driver.py     # Gemini OpenAI-compat driver
  internal_agents/
    planner.py           # LLM-driven Planner v0.2 (retry + fallback + cache)
  telemetry/
    jsonl.py             # JSONLHook: write events per-date
    consumer.py          # TelemetryConsumer: read + summary + cost + latency
  bus/
    jsonl.py             # JSONLBus: append-only message log
    watch.py             # BusWatcher: file polling for BusLoop integration
  schemas/
    a2a.py               # sessionKey builder (task:<id>:stage:<id>)
    artifact.py          # Artifact Pydantic model
    message.py           # Message + MessageType + Priority
    dag.py               # TaskDAG + DAGNode for Planner output
  cli.py                 # agentos CLI: send / receive / show / search / inbox
  api/                   # (TBD) FastAPI routes + WebSocket events

tests/                    # ~150 tests
docs/
  ADR/                    # 10 ADRs (0001-0011)
  01-protocol-v0.1.md    # Frozen decision summary
  02-bootstrap.md        # Setup + GitHub push pitfalls
  03-dogfood-bus.md      # Bus dogfood workflow
examples/
  demo_bus_loop.py        # Plan B end-to-end (TASK_REQUEST -> reply)
  demo_dogfood.py         # Pure Engine 4-stage DAG + partial-success replay
```

## Quick Start

```bash
git clone [email protected]:increase2002/AgentOS.git
cd AgentOS
pip install -e .[dev]
pytest
```

Full setup including GitHub push pitfalls: see [docs/02-bootstrap.md](docs/02-bootstrap.md).

Dogfooding the bus yourself: see [docs/03-dogfood-bus.md](docs/03-dogfood-bus.md).

## Architecture Decision Records

10 ADRs in `docs/ADR/`:
- [0001](docs/ADR/0001-integration-method.md) Integration Method (HTTP + OpenAI-compat)
- [0002](docs/ADR/0002-context-handoff.md) Context Handoff (Artifact + summary, no history)
- [0003](docs/ADR/0003-internal-sub-agents.md) Internal Sub-Agents (5 roles, code > small > flagship)
- [0004](docs/ADR/0004-evaluation-loop.md) Evaluation Loop (multi-source signals, per-stage)
- [0005](docs/ADR/0005-memory-federation.md) Memory Federation (fan-out + rerank, Plan B)
- [0006](docs/ADR/0006-concurrency-streaming.md) Concurrency & Streaming (Budget=4, streaming=1 slot)
- [0007](docs/ADR/0007-driver-failure-policy.md) Driver Failure Policy (fail-fast + retry, no auto-fallback)
- [0008](docs/ADR/0008-artifact-storage.md) Artifact Storage (local FS MVP)
- [0009](docs/ADR/0009-tool-subset-enforcement.md) Tool Subset Enforcement (plan-only / read-only)
- [0010](docs/ADR/0010-orchestrator-engine.md) Orchestrator Engine (Plan B accepted)
- [0011](docs/ADR/0011-memory-backend-tiering.md) Memory Backend Tiering (Real / Synthetic / Empty)

Frozen summary: [docs/01-protocol-v0.1.md](docs/01-protocol-v0.1.md).

## License

MIT
# AgentOS

Multi-agent collaboration OS that orchestrates external AI agents (OpenClaw, Codex, Claude, Gemini, ...) and built-in sub-agents into a single coherent team.

> Agent is the employee, Orchestrator is the management system, Memory is the enterprise knowledge base, Communication Bus is the internal collaboration network.

## Status

**v0.2 shipped.** Protocol frozen (11 ADRs). All four vendor drivers (OpenClaw / Codex / Anthropic / Gemini) work. Orchestrator Engine + Planner + Memory Service + Telemetry + JSONL Bus + `agentos` CLI all in place. ADR-0012 (Sidecar BusLoops + Agent Autonomy) Accepted — D1 `bus-watch-codex` and D2 `bus-poll` shipped.

- **247 tests passing** across 18 test modules
- **~4500 LOC** across `src/agentos/`
- **2 demos**: `examples/demo_bus_loop.py` (Plan B full loop), `examples/demo_dogfood.py` (pure Engine 4-stage DAG + partial-success replay)
- **1 working example**: `examples/c1_real_e2e.py` (MemoryService + OpenClaw real E2E)

## Architecture

```
User
  -> Web / API / CLI
  -> AgentOS Control Plane
     - Planner (LLM-driven, v0.2)
     - Orchestrator Engine (DAG executor, BusLoop-driven)
     - Agent Manager / registry
     - Memory Service (cross-agent federation per ADR-0005)
     - Telemetry hook (JSONL append-only, per ADR-0004)
  -> A2A Communication Bus (JSONL append-only log at G:/AgentOS/.agentos/bus.jsonl)
  -> Sidecar BusLoops (per-agent listeners per ADR-0012)
  -> External Agents (OpenClaw / Codex / Claude / Gemini / ...)
  -> Tools Layer (Browser / Terminal / Git / Cloud / API)
```

Data flow (per ADR-0012):
1. `TASK_REQUEST` arrives on bus (`G:/AgentOS/.agentos/bus.jsonl`)
2. Orchestrator BusLoop picks it up, calls `Engine.run(task_id, dag)`
3. `Planner` (v0.2 LLM-driven) produces `TaskDAG`
4. `DAGRunner` walks DAG topologically (parallel_group, Semaphore(4), retry+backoff)
5. `Engine._dispatch_stage` calls `await driver.chat(brief, ...)` (with `tool_subset` enforced)
6. `JSONLHook` records `DRIVER_CHAT_IN/OUT`, `STAGE_START/END`, `ERROR` to `G:/AgentOS/telemetry/{date}.jsonl`
7. `CheckpointStore` persists per-stage results for partial-success replay
8. `TASK_PROGRESS` (per stage) + `TASK_ACCEPT` (terminal) emitted back to bus
9. Sidecar BusLoops (one per agent) poll bus for their own inbox, inject as system events

## Driver Matrix

| Agent | Driver | Protocol |
|---|---|---|
| **OpenClaw** (chat) | `OpenClawDriver` (subclass of `OpenAIDriver`) | OpenAI-compatible HTTP Contract B (`/v1/chat/completions`) |
| **OpenClaw** (node capabilities) | `WSDriver` | Native WebSocket gateway (Contract A) |
| **Codex** | `CodexAdapter` | FastAPI wrapping Codex CLI subprocess (config-driven invocation) |
| **Anthropic (Claude)** | `AnthropicDriver` | Anthropic Messages API native, format-converted at boundary |
| **Google Gemini** | `GeminiDriver` | OpenAI-compatible endpoint (`/v1beta/openai/`) |

All drivers expose `async chat(brief, attachments=None, session_key=None, tool_subset=None) -> ChatResult`.
`tool_subset` enforces plan-only / read-only via system-prompt injection (ADR-0009 MVP, soft constraint).

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
    openclaw_adapter.py  # Wraps OpenClawMemoryDriver for MemoryService consumption
    empty_drivers.py     # EmptyMemoryDriver + Codex/Anthropic/Gemini (per ADR-0011)
    openclaw_token.py    # resolve_openclaw_token() canonical-path helper
  drivers/
    base.py              # BaseDriver abstract + ChatResult + DriverError
    openai_driver.py     # OpenAI-compatible driver (tool_subset enforcement)
    ws_driver.py         # WebSocket driver for OpenClaw native gateway
    openclaw_driver.py   # OpenClaw-specific subclass (auto-installs telemetry)
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
                         # / watch / bus-watch-codex / bus-poll
examples/
  demo_bus_loop.py       # Plan B full loop (TASK_REQUEST -> Engine -> reply)
  demo_dogfood.py        # Pure Engine 4-stage DAG + partial-success replay
  c1_real_e2e.py         # MemoryService + OpenClaw real backend E2E verification
tests/                   # 247 tests across 18 modules
docs/
  ADR/                   # 11 ADRs (0001-0011)
  01-protocol-v0.1.md   # Frozen decision summary
  02-bootstrap.md       # Setup + GitHub push pitfalls
  03-dogfood-bus.md     # Bus dogfood workflow
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

11 ADRs in `docs/ADR/`:
- [0001](docs/ADR/0001-integration-method.md) Integration Method (HTTP + OpenAI-compat)
- [0002](docs/ADR/0002-context-handoff.md) Context Handoff (Artifact + summary, no history)
- [0003](docs/ADR/0003-internal-sub-agents.md) Internal Sub-Agents (5 roles, code > small > flagship)
- [0004](docs/ADR/0004-evaluation-loop.md) Evaluation Loop (multi-source signals, per-stage)
- [0005](docs/ADR/0005-memory-federation.md) Memory Federation (fan-out + rerank, Plan B)
- [0006](docs/ADR/0006-concurrency-streaming.md) Concurrency & Streaming (Budget=4, streaming=1 slot)
- [0007](docs/ADR/0007-driver-failure-policy.md) Driver Failure Policy (fail-fast + retry)
- [0008](docs/ADR/0008-artifact-storage.md) Artifact Storage (local FS MVP)
- [0009](docs/ADR/0009-tool-subset-enforcement.md) Tool Subset Enforcement (plan-only / read-only)
- [0010](docs/ADR/0010-orchestrator-engine.md) Orchestrator Engine (Accepted 2026-07-26)
- [0011](docs/ADR/0011-memory-backend-tiering.md) Memory Backend Tiering (Real / Synthetic / Empty)
- [0012](docs/ADR/0012-sidecar-busloops-and-agent-autonomy.md) Sidecar BusLoops + Agent Autonomy (Accepted 2026-08-22)

Frozen summary: [docs/01-protocol-v0.1.md](docs/01-protocol-v0.1.md).

## License

MIT
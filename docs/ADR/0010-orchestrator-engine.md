# ADR-0010: Orchestrator Engine

- **Status**: Proposed (Codex / OpenClaw agreement pending Phase 3 dogfood validation)
- **Date**: 2026-07-26
- **Deciders**: Codex, OpenClaw (龙大), Increase (老大)

## Context

The Bus + CLI bootstrap (Plan A, shipped in `bb12c3b` + `f44903d` + `f572846`) lets humans ferry messages between Codex and OpenClaw via the JSONL bus. This works for the bootstrap phase but does not scale: a multi-stage task needs an automated scheduler to:

1. Run stages in parallel where independent (`parallel` branch in DAG).
2. Enforce Concurrency Budget (ADR-0006) so 8 simultaneous `Parallel` stages do not saturate the host.
3. Implement partial-success checkpointing (ADR-0007 + Phase-2 conversation): if stage 3 of 5 fails after retry, stages 1-2 must not re-run from scratch.
4. Maintain session-key routing (`task:<id>:stage:<id>[:sub:<id>]` per `schemas/message.py`).
5. Wire the telemetry hook (per Codex Q-F) so every driver call + bus event lands in `G:/AgentOS/telemetry/{date}.jsonl` for ADR-0004 evaluation.
6. Read from the same bus file the CLI uses, so the human ferry workflow and the automated orchestrator share one source of truth.

Without an Orchestrator, "Plan B" from `docs/03-dogfood-bus.md` (Codex doc) stays a wish. Without it, AgentOS is a chat ferry, not a multi-agent platform.

## Decision

**Build `agentos.orchestrator.Engine` — an async DAG executor that reads the Bus, schedules stages via drivers, enforces budgets, and writes telemetry.**

### Module layout

```
src/agentos/orchestrator/
  __init__.py
  engine.py          # Engine class — main entry point
  dag_runner.py      # Stage DAG walker (topological order + parallel branch)
  checkpoint.py      # TaskCheckpointStore — partial-success persistence
  session_keys.py    # Session-key builder + validator (task:<id>:stage:<id>)
  bus_loop.py        # Polls bus.jsonl via BusWatcher, dispatches to Engine
```

### Core API

```python
class Engine:
    def __init__(
        self,
        *,
        drivers: dict[str, BaseDriver],   # name -> driver instance
        memory: MemoryService | None = None,
        planner: Planner | None = None,    # produces DAG from task brief
        bus: JSONLBus | None = None,
        telemetry: JSONLHook | None = None,
        concurrency_budget: int = 4,       # per ADR-0006
        checkpoint_dir: Path = Path("G:/AgentOS/.agentos/checkpoints"),
    ) -> None: ...

    async def run(self, task_id: str, brief: str | None = None) -> TaskResult:
        """Execute a task end-to-end. Idempotent: re-running picks up from
        the latest checkpoint. Returns TaskResult with stage statuses +
        cost summary.
        """

    async def poll(self) -> None:
        """Block on the bus, dispatch new TASK_REQUEST messages to run().
        Used as the long-running daemon entry point.
        """
```

### Lifecycle

1. **Receive** — `poll()` reads new `TASK_REQUEST` messages from bus.jsonl
   (via `BusWatcher` from `agentos.bus.watch`).
2. **Plan** — `Planner` (in `internal-agents/planner.py`) turns the brief
   into a `PlanDAG` (per `schemas/dag.py`).
3. **Checkpoint** — Persist `task_id -> DAG` to
   `G:/AgentOS/.agentos/checkpoints/{task_id}.json`. Re-running `run()`
   loads the DAG instead of re-planning.
4. **Execute** — `DAGRunner` walks the DAG in topological order, launching
   stages in `Parallel` mode concurrently via `asyncio.gather`. Each stage
   is gated by `asyncio.Semaphore(concurrency_budget)`.
5. **Driver call** — `Engine._dispatch_stage` calls
   `await driver.chat(brief, session_key=..., tool_subset=...)`. Streaming
   responses acquire one semaphore slot for the whole round (per ADR-0006).
6. **Telemetry** — Before/after each driver call, `JSONLHook` records
   `DRIVER_CHAT_IN`/`DRIVER_CHAT_OUT` (per `telemetry/jsonl.py`). Stage
   transitions emit `STAGE_START`/`STAGE_END`.
7. **Checkpoint per stage** — On stage success, write
   `{task_id}/{stage_id}/result.json` to checkpoint dir. On failure,
   record the error and stop (fail-fast per ADR-0007).
8. **Reply** — Emit a `TASK_PROGRESS` (per stage) and a final
   `TASK_ACCEPT` (with `artifact_ref` to the result) onto the bus.
9. **Retry** — On retryable error (configurable per driver), retry up to
   `retry.max_attempts=3` with exponential backoff `1s/2s/4s + jitter`
   (per ADR-0007). Fallback to another driver requires explicit
   `fallback_drivers=[...]` config (off by default).

### Concurrency

* Single `asyncio.Semaphore(4)` wraps every driver call.
* Streaming responses hold the slot for the full round (not per chunk).
* Parallel stages share the budget via `asyncio.gather` + semaphore.
* Per-host budget configurable in `engine.concurrency_budget`.

### Partial success

* Stage results written to `G:/AgentOS/.agentos/checkpoints/{task_id}/{stage_id}/`.
* On engine restart, `run(task_id)` reloads completed stages and only
  re-runs pending/failed ones (unless `--force-rerun` flag).
* Checkpoint format: `{stage_id, status, result, retries, cost}` per stage.

### Session keys

* Built by `session_keys.build(task_id, stage_id, sub_id=None) -> str`.
* Validated against reserved prefixes (`subagent:`, `cron:`, `acp:`).
* Length ≤ 128 chars (matches `schemas/message.py` constraints).
* Stage key: `task:{task_id}:stage:{stage_id}`.
* Sub-task key (e.g. parallel branch): `task:{task_id}:stage:{stage_id}:sub:{sub_id}`.

### Error handling

* Fail-fast by default (`fail_fast=True`): one stage error stops the task.
* Retry: 3 attempts, exponential backoff 1s/2s/4s + jitter.
* Fallback: opt-in via `fallback_drivers=[...]`; not driver-internal.
* Errors emit `ERROR` telemetry event with exception type + 500-char message.

### Telemetry integration

* One `JSONLHook` per Engine; shared across all drivers via `wrap_driver`.
* Every driver call produces 2 events (`DRIVER_CHAT_IN` + `DRIVER_CHAT_OUT`)
  with `session_key`, `driver`, `latency_ms`, `token_usage`.
* Bus activity produces `BUS_MESSAGE_IN`/`BUS_MESSAGE_OUT`.
* Errors produce `ERROR` events.
* Eval loop (ADR-0004) reads these JSONL files; no separate export path.

### Bus integration

* `Engine.poll()` uses `BusWatcher.watch_async()` to listen for
  `TASK_REQUEST` messages.
* `Engine` writes replies back via `JSONLBus.append()`.
* Bus file path shared with CLI (`G:/AgentOS/.agentos/bus.jsonl`).
* If Plan B moves to Redis Streams later (per Codex `03-dogfood-bus.md`),
  the persistence layer swaps; data model unchanged.

### Out of scope (deferred to v0.2)

* Debate mode (multi-agent voting on decisions) — separate `DebateRunner`.
* Cost controller (per-task budget enforcement) — separate `CostGate`.
* Streaming partial responses to Bus (SSE chunks).
* Cross-host worker pool (multi-process orchestration).
* Hot reload of driver config without restart.

## Consequences

**Positive**
- Replaces the human ferry with an automated loop; bootstrap loop now
  has a clear upgrade path.
- Plan B shares the bus file with Plan A (CLI); humans and orchestrator
  can coexist during gradual rollout.
- Telemetry + checkpoints make partial-success and cost attribution
  debuggable from day one (per ADR-0004 acceptance criteria).
- Session-key namespace aligns with `schemas/message.py`; no duplication.
- Async driver interface from Codex vendor wrappers is used directly.

**Negative**
- Adds 4 new modules + DAG runner + checkpoint store — ~600 LOC.
- Checkpoint writes add I/O per stage; cost ~5-10ms per stage on Windows
  SSD.
- `asyncio.Semaphore(4)` may queue bursty loads; latency tail risk.
- DAG re-planning on retry uses cached plan; if upstream brief changes
  mid-run, behavior is undefined (forces a new task_id).

**Mitigations**
- Configurable budget per host (ADR-0006 follow-up already tracked).
- `--force-rerun` flag bypasses checkpoint for full re-execution.
- Checkpoint GC: clean up after 30 days (matches ADR-0008 artifact GC).
- Future: stream checkpoints to disk async (deferred to v0.2).

## Alternatives Considered

- **A. Cron-style scheduler (Celery / APScheduler).** Heavy dependency
  surface; Celery needs Redis/RabbitMQ. Overkill for MVP. Rejected.
- **B. Polling-based loop without DAG.** Simpler but loses parallel
  branch + partial success; would need rewrite later. Rejected.
- **C. Reactive actor model (e.g. Ray, Pykka).** Distributed-first
  design assumes multi-host. We need single-host MVP first. Deferred to
  v0.2 if multi-host demand materializes.
- **D. Reuse LangGraph / LangChain orchestration.** Vendor lock-in;
  pulls in transitive deps (langchain-core, pydantic v1 compat).
  Codex AgentOS should be framework-agnostic. Rejected.
- **E. Bus watcher daemon + manual stage runner (chosen).** Lightweight
  (~600 LOC), no new deps, fits async driver interface, shares Bus with
  CLI. Aligns with Phase 1 infrastructure already shipped.

## Implementation plan (3-4h)

1. `orchestrator/__init__.py` + `engine.py` (Engine class skeleton) — 30min.
2. `session_keys.py` (build + validate) — 15min.
3. `checkpoint.py` (TaskCheckpointStore) — 30min.
4. `dag_runner.py` (topological walk + parallel gather + semaphore) — 1h.
5. `bus_loop.py` (BusWatcher integration + reply dispatch) — 30min.
6. `internal-agents/planner.py` (LLM + schema produces PlanDAG; thin
   wrapper around existing `schemas/dag.py`) — 45min.
7. Tests: `test_engine.py`, `test_dag_runner.py`, `test_checkpoint.py`,
   `test_session_keys.py`, `test_bus_loop.py` — 45min.
8. Smoke test: end-to-end 3-stage DAG with real OpenClaw driver — 15min.

## Validation

- 3-stage DAG (research → write → review) running on `agentos run`
  CLI subcommand.
- Telemetry file has `DRIVER_CHAT_IN/OUT` for every driver call +
  `STAGE_START/END` for every stage + `BUS_MESSAGE_OUT` for replies.
- Re-running same task_id after engine restart skips completed stages
  (partial-success verification).
- Concurrency budget enforced: 8 parallel stages show 4 concurrent
  in `telemetry/{date}.jsonl` timestamps.

## References

- ADR-0001 (Integration Method - Bus + OpenAI-compat)
- ADR-0006 (Concurrency & Streaming)
- ADR-0007 (Driver Failure Policy)
- ADR-0008 (Artifact Storage)
- ADR-0009 (Tool Subset Enforcement)
- ADR-0011 (Memory Backend Tiering)
- `docs/03-dogfood-bus.md` Plan B roadmap (Codex)
- `schemas/dag.py` (Codex) — PlanDAG schema
- `schemas/message.py` — Bus message envelope
- `agentos/bus/watch.py` — BusWatcher (Phase 1, this commit series)
- `agentos/telemetry/jsonl.py` — JSONLHook (Phase 1, this commit series)
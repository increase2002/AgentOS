# ADR-0012: Sidecar BusLoops + Agent Autonomy

- **Status**: Proposed (skeleton — Codex to fill)
- **Date**: 2026-08-16
- **Deciders**: Codex, OpenClaw (龙大), Increase (老大)

## Context and Problem Statement

v0.1 (ADR-0001..0011) ships a working **RPC-style orchestration**: humans ferry messages via `agentos` CLI, the BusLoop watches for `TASK_REQUEST` to `orchestrator`, the Engine dispatches DAGs through drivers, replies land back on `bus.jsonl`. Every cycle still requires human copy-paste between Codex and OpenClaw sessions.

老大 explicitly asked: *"Can the OS run so you and Codex talk without me ferrying?"*

**v0.1 answers: No.** `examples/demo_bus_loop.py` still documents `老大 ferries Codex reply -> bus -> BusLoop -> Engine -> reply -> bus -> 老大 ferries back`.

**This ADR targets v0.2** — a Sidecar BusLoop model where each agent runs its own listener, message-injection bridges replace human ferrying, and the human drops from "copy-paste master" to "press Enter at most once per cycle".

## Decision Drivers

- 老大 wants minimum manual effort (target: 1 Enter press per Codex cycle, ideally zero)
- Codex is an **interactive agent** on Windows, not a daemon — fundamentally limits Codex-side automation
- OpenClaw is a long-running process on Windows with cron + systemEvent injection — full sidecar model possible
- MemoryService + OpenClawMemoryAdapter 真 E2E shipped in `7299230` — orchestration substrate is ready

## Considered Options

### Option A: D3 — Codex 真 daemon (REJECTED on Windows)
- `codex app-server daemon` exists but **no Windows lifecycle implementation** (Codex verify 2026-08-16)
- Only path to D3: Codex CLI ships Windows daemon support, or host migrates to Linux/macOS
- 老大 work: 0
- Status: **永久 blocked on Windows**; ADR records this constraint

### Option B: D1 + D2 (CHOSEN) — Codex queue processor + OpenClaw sidecar
- **D1 (Codex side)**: small script `agentos bus-watch-codex` watches `bus.jsonl` for `to_agent=codex`, appends to `inbox_codex.md`. Codex每次 turn 起来读 inbox → 处理 → 写回 bus → 清空 inbox. **Not a true sidecar — a next-turn batch processor.**
- **D2 (OpenClaw side)**: BusLoop参数化 `to_agent=openclaw` + 多消息类型 dispatch; cron + systemEvent 桥接 bus → main session
- 老大 work: 1 Enter press per Codex cycle (still ferry mode but copy-paste is automated)
- Status: ships in v0.2

### Option C: Pure message-type expansion, no sidecar (CONSIDERED, REJECTED)
- Just add KNOWLEDGE_SHARE / REVIEW_REQUEST / HANDOFF handling to existing BusLoop, still orchestrator-only
- Doesn't solve 老大 copying — only adds new message flavors
- Status: deprecated

## Decision

**Adopt Option B (D1 + D2).** This is the **incremental, shippable** path to "老大按回车".

---

## Implementation Plan (SKELETON — Codex to fill)

### 1. Sidecar BusLoop Abstraction

> *OpenClaw fills (this section was skeleton-placeholder): API shape + state machine*

**Current state**: `BusLoop.__init__` hardcodes `watch_to_agent=ORCHESTRATOR_AGENT_NAME` and `watch_message_type=MessageType.TASK_REQUEST.value`. Needs parameterization.

**Proposed API**:
```python
class BusLoop:
    def __init__(
        self, engine: "Engine", *,
        bus: JSONLBus | None = None,
        watch_to_agent: str,                    # CHANGED: any agent name
        watch_message_types: list[str],          # CHANGED: list, not single
        dispatch_handlers: dict[str, HandlerFn], # NEW: type -> handler map
        poll_interval_s: float = 1.0,
    ) -> None: ...
```

- **Backward compat**: default `watch_message_types=[MessageType.TASK_REQUEST.value]` keeps existing orchestrator behavior
- **No new endpoint types** in this ADR — just new dispatch handlers

### 2. OpenClaw Sidecar Implementation Path

> *OpenClaw fills (this section was skeleton-placeholder): concrete steps + cron config + systemEvent format*

**Components needed**:

a. **Sidecar BusLoop instance** with `watch_to_agent="openclaw"`, watching `KNOWLEDGE_SHARE` + `REVIEW_REQUEST` + `HANDOFF` + `TASK_REQUEST`
b. **Bus → session bridge**: cron job (interval ~5s) reads new `to_agent=openclaw` messages, formats as `systemEvent`, injects into main session
c. **Reply mechanism**: session replies go through normal main-session message flow; a small handler publishes back to `bus.jsonl` with `from_agent="openclaw"`
d. **Conflict avoidance**: cron must not race with main session. Use OpenClaw's `sessions_send` to inject as system event (out-of-band from user input)

**Estimate**: ~2h implementation + ~30 min testing

### 3. Codex Queue Processor Implementation Path

> *Codex fills (this section was skeleton-placeholder): concrete steps + Codex-side semantics*

**Components needed**:

a. **`agentos bus-watch-codex`** subcommand: tail `bus.jsonl`, filter `to_agent=codex`, append new messages to `inbox_codex.md` (project-local, gitignored)
b. **Codex turn-time workflow**: Codex's main session bootstrap reads `inbox_codex.md` if present, treats it as context, processes, writes reply to `bus.jsonl` with `from_agent="codex"`, truncates `inbox_codex.md` after handling
c. **No true daemon** — Codex is interactive; queue processor is "next-turn handler"
d. **No automatic trigger** — 用户 must press Enter to invoke Codex. This is the **1-Enter-press-per-cycle** tax

**Estimate**: ~30 min + tests

### 4. Message Type Dispatch Matrix

> *Codex fills (this section was skeleton-placeholder): per-type decision table*

**Skeleton table** (Codex to expand):

| Message Type | BusLoop dispatch | Engine / Driver? | Reply type |
|---|---|---|---|
| TASK_REQUEST | Engine.run(DAG) | Yes | TASK_PROGRESS / TASK_ACCEPT / TASK_BLOCKED |
| KNOWLEDGE_SHARE | Direct to agent session (no Engine) | No | (optional) KNOWLEDGE_SHARE |
| REVIEW_REQUEST | Direct to agent session | No | KNOWLEDGE_SHARE with reviewed content |
| HANDOFF | Direct to agent session (carries task context) | No | TASK_ACCEPT or TASK_REQUEST (re-dispatch) |
| DECISION | Direct to agent session | No | DECISION |
| TASK_PROGRESS / ACCEPT / BLOCKED | (no dispatch, terminal messages) | — | — |

### 5. First Dogfood Use Case

> *OpenClaw fills (this section was skeleton-placeholder): the closed-loop test*

**Scenario**: Use ADR-0012 review itself as the dogfood case.

1. Codex reviews this ADR (this is in v0.1 cycle, no sidecar yet — same as now)
2. Codex publishes `REVIEW_REQUEST` to bus: `from_agent=codex`, `to_agent=openclaw`, payload `{adr_path: "docs/ADR/0012-...", question: "Is the sidecar model complete?"}`
3. OpenClaw sidecar (D2) picks up `REVIEW_REQUEST`, injects into main session
4. Main session processes, writes `KNOWLEDGE_SHARE` reply: `from_agent=openclaw`, `to_agent=codex`, payload `{answer: "..."}`
5. Codex's next turn (D1) reads `inbox_codex.md`, sees reply, continues review

**老大 actions required**: 1 Enter press per Codex turn. **Net reduction**: from "copy-paste full thread" to "press Enter".

### 6. Codex 半自动 — Explicit Limitation

> *OpenClaw fills (this section was skeleton-placeholder): transparent disclosure*

This section must explicitly state for stakeholders:

- Codex **cannot be a true sidecar** on Windows as of 2026-08-16 (no daemon lifecycle)
- The D1 queue processor **still requires user invocation** (1 Enter per cycle)
- D3 is **permanently blocked on Windows** until Codex CLI ships Windows daemon
- This ADR does **not** achieve "zero 老大 actions" — that requires D3 or equivalent

This section is non-negotiable transparency — must not be omitted in any ADR public version.

### 7. v0.2 → v0.3 Upgrade Conditions

> *Codex fills (this section was skeleton-placeholder): trigger conditions for next iteration*

**D3 unlocks when** (any of):
- Codex CLI ships Windows daemon lifecycle implementation
- Host migrates to Linux/macOS
- Codex spawns a child process with persistent WS connection (third-party tooling)

**Once D3 unlocks**: this ADR is superseded by ADR-0013 (true sidecar for both agents). D1 + D2 stay as fallback.

---

## Open Questions

- How does OpenClaw's main session distinguish system-injected bus messages from user input? (Format must not be confused with user commands)
- Where does `inbox_codex.md` live? Project-local? Per-Codex-session? User-global? (Codex to decide in section 3)
- Do we need a `last_seen_message_id` per agent to avoid reprocessing? Or rely on `bus.jsonl` since_id filter?

## Consequences

### Positive

- 老大 drops from "copy-paste master" to "press Enter once per Codex cycle"
- Bus becomes a real communication substrate, not a ferry log
- Dogfood workflow self-validates the architecture (ADR-0012 reviews itself)
- MemoryService真 backend + sidecar = cross-agent context sharing viable

### Negative

- 1-Enter tax remains until D3 (Codex daemon)
- Codex half-automation complicates "who is responsible for reply latency" framing
- bus.jsonl may grow quickly with sidecar messages — needs size monitoring

### Neutral

- ADR-0012 itself is the first dogfood use case — reviewer must understand the bus semantics
- D1/D2 introduce a new failure mode: stale inbox if Codex doesn't drain it

---

## References

- ADR-0001: Integration Method (Contract B for OpenClaw, OpenAI-compat)
- ADR-0002: Context Handoff (Artifact + structured summary)
- ADR-0003: Internal Sub-Agents (5-role model)
- ADR-0004: Evaluation Loop
- ADR-0005: Memory Federation (B方案)
- ADR-0006: Concurrency + Streaming
- ADR-0007: Driver Failure Policy
- ADR-0008: Artifact Storage
- ADR-0009: Tool Subset Enforcement
- ADR-0010: Orchestrator Engine (BusLoop semantics)
- ADR-0011: Memory Backend Tiering
- `examples/demo_bus_loop.py` (v0.1 ferry baseline)
- `examples/c1_real_e2e.py` (`7299230` — closed-loop memory federation proof)
- Codex D3 FAIL note (2026-08-16 verify — `codex app-server daemon` no Windows impl)
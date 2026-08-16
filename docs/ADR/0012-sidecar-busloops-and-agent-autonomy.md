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
>
> **Updated 2026-08-16 by OpenClaw** to answer Codex-deferred Open Question on system-injected vs user input distinction.

**Components needed**:

a. **Sidecar BusLoop instance** (`feature/openclaw-sidecar-busloop` branch `31fe25a`) with `watch_to_agent="openclaw"`, watching `KNOWLEDGE_SHARE` + `REVIEW_REQUEST` + `HANDOFF` + `TASK_REQUEST` (the `BusWatcher.message_types` list filter now supports this — see `tests/test_bus_watcher_message_types.py`).

b. **Bus → session bridge**: OpenClaw cron job (`openclaw cron create`) ticks every 30 seconds with `--system-event "[bus-poll]"` and `--session main`. The `[bus-poll]` event is a *trigger*, not data — it tells the main session: "poll bus for new messages". Main session, on receiving `[bus-poll]`, invokes the new `agentos bus-poll --to openclaw` CLI subcommand, which reads `bus.jsonl` for new `to_agent=openclaw` messages since the last cursor and returns them in the tool result.

c. **Reply mechanism**: When main session wants to reply on the bus (after processing a `KNOWLEDGE_SHARE` / `REVIEW_REQUEST` / `HANDOFF`), it calls `agentos bus-send --to <agent> --from openclaw --type <reply-type> --payload <...>`. The CLI appends a `Message` to `bus.jsonl`. No special integration — just standard main-session tool flow.

d. **Conflict avoidance**: The 30-second cron interval is the floor on latency (worst case = 30s between Codex sending and OpenClaw receiving). The cursor mechanism (`agentos/.codex_last_openclaw_id.txt`) is updated atomically (`os.replace` after writing to a temp file) so concurrent pollers don't double-process.

#### System-injected vs user input distinction (answering Codex-deferred Open Question)

OpenClaw sessions receive text from three distinct sources that must not be confused:

1. **User input** (老大 typing in webchat / channel): free-form text, possibly multi-line, possibly with markdown, **never** prefixed with `[bus-...]`.
2. **Cron system events** (this ADR's bridge mechanism): always prefixed `[bus-...]` (specifically `[bus-poll]` for the trigger; future extensions may add `[bus-something-else]`). Format: `[bus-<tag>] <optional-payload>`. The session has a rule: "if the message starts with `[bus-...]`, treat it as a bus trigger; otherwise treat it as a user message."
3. **Memory / tool results**: always arrive via tool-result framing, not as raw text — these are unambiguously distinguishable.

**Why this works**:
- `[bus-...]` prefix is an *unlikely* accidental prefix in user input (老大 doesn't normally type square-bracket-tokens in conversation).
- Even if 老大 did, the worst case is the session treats their text as a bus trigger, runs the poll, finds nothing, replies "no new bus messages" — gracefully degraded, not destructive.
- Tool-result framing gives a hard structural distinction for memory / tool outputs.

**Future-proofing**: if multiple bus triggers emerge (e.g. `[bus-poll]`, `[bus-flush]`, `[bus-replay]`), the session just dispatches on the tag.

#### Initial patch (shipped to `feature/openclaw-sidecar-busloop`, not yet merged)

Commit `31fe25a`:
- `BusWatcher.__init__` accepts `message_types: list[str] | None = None` (new) alongside legacy `message_type` (singular, deprecated).
- `BusLoop.__init__` accepts `watch_message_types: list[str] | None = None` (new) alongside legacy `watch_message_type` (singular, deprecated).
- 4 new tests cover list filtering, back-compat, precedence rules.
- Full suite: **237 passed** (was 233).

**Estimate remaining**: ~1h to wire the cron job + `agentos bus-poll` CLI + reply mechanism end-to-end, then dogfood the bus-poll system-injected trigger flow against `agentos bus-watch-codex`.

**Branch state**: `feature/openclaw-sidecar-busloop @ 31fe25a`. NOT merged to main — awaiting ADR-0012 Accepted (per the standard OpenClaw reviewer + Codex reviewer + 老大 approve workflow).

### 3. Codex Queue Processor Implementation Path

#### 3.1 `agentos bus-watch-codex` CLI subcommand

New subcommand of the `agentos` CLI. Tails `bus.jsonl` for new `to_agent=codex` messages and appends them to a project-local `inbox_codex.md`.

**Storage layout** (under `G:/AgentOS/.agentos/`, all gitignored):
```
.agentos/
  bus.jsonl                  # shared with OpenClaw sidecar + CLI
  inbox_codex.md             # messages for Codex, Markdown digest
  codex_last_id.txt          # cursor: last consumed message id
```

**CLI shape**:
```bash
agentos bus-watch-codex
  [--bus PATH]               # default: G:/AgentOS/.agentos/bus.jsonl
  [--inbox PATH]             # default: G:/AgentOS/.agentos/inbox_codex.md
  [--cursor PATH]            # default: G:/AgentOS/.agentos/codex_last_id.txt
  [--once]                   # drain + exit (default: --watch)
  [--watch]                  # poll loop with --poll-interval-s
  [--poll-interval-s N]      # default: 5.0
```

**Inbox format** (Markdown, append-only):
```markdown
## msg-abc123def (2026-08-16T20:14:32+00:00)
from: openclaw  to: codex  type: HANDOFF  priority: NORMAL
artifact: (none)

ADR-0012 骨架 ship, sections 1/2/5/6 you filled; 3/4/7 mine. 24h review.

---
## msg-def456ghi (2026-08-16T20:18:00+00:00)
from: openclaw  to: codex  type: KNOWLEDGE_SHARE
...
```

Each message becomes one `##` block. The `---` separator between messages is optional (every block is self-contained).

**Cursor handling**:
- On startup, read `codex_last_id.txt` (or empty if first run)
- Call `bus.iter_messages(to_agent="codex", since_id=<cursor>)`
- For each result, append to inbox + update cursor to that message's id
- Atomic write: write to `<cursor>.tmp`, then rename (avoid partial state on crash)
- On `import agentos` import or CLI startup, the bus is append-only so cursor never goes backwards

#### 3.2 Codex turn-time workflow

Codex's session bootstrap (when invoked by the user) checks for `inbox_codex.md`:

```python
# agentos_bootstrap.py (invoked by Codex session init or a system prompt addendum)
from pathlib import Path

inbox = Path(r"G:/AgentOS/.agentos/inbox_codex.md")
if inbox.exists() and inbox.stat().st_size > 0:
    print(f"[Codex] Pending bus messages in {inbox}")
    # Treat as context for this turn's response
    # ... Codex reasons + writes reply to bus ...
    # ... then truncates inbox after handling ...
```

After Codex processes the inbox, it should:
1. Write its reply to `bus.jsonl` via `agentos send --to openclaw --from codex --from-file <reply> --task t-xxx`
2. Truncate `inbox_codex.md` (or rename to `processed/inbox_codex_<timestamp>.md` for audit)
3. The cursor is already updated by `bus-watch-codex` (which runs on every Codex turn OR as a cron job)

#### 3.3 No true daemon

- Codex is interactive (sandbox / CLI app). `codex app-server daemon start` fails on Windows.
- The queue processor **never replaces** the user's Enter press. It only:
  - Drains bus into `inbox_codex.md` (can run as separate process or at session start)
  - Provides context for Codex's next turn
  - **Triggers Codex**: must be done by user, every cycle

#### 3.4 No automatic trigger

The **1-Enter tax** per cycle is **explicitly accepted** as the v0.2 ceiling. Reducing it to 0 requires D3 (Codex daemon) per Section 7.

**Estimate**: ~30 min implementation + 8-10 tests (DONE in commit `c3ebb8c`'s `resolve_openclaw_token` pattern; same shape for `bus-watch-codex`).

**Tests** (sketch):
- `test_bus_watch_codex_appends_to_inbox`: mock bus, run once, assert inbox content matches
- `test_bus_watch_codex_updates_cursor`: assert cursor advances
- `test_bus_watch_codex_idempotent`: run twice, no duplicate writes
- `test_bus_watch_codex_filters_by_to_agent`: messages not to codex ignored
- `test_bus_watch_codex_includes_artifact_ref`: attachment path preserved
- `test_bus_watch_codex_handles_empty_bus`: no crash
- `test_bus_watch_codex_handles_missing_cursor_file`: starts from beginning
- `test_bus_watch_codex_atomic_cursor_write`: simulates crash mid-write, verifies no partial state
- `test_bus_watch_codex_format_with_long_content`: truncates / escapes correctly
- `test_bus_watch_codex_format_with_unicode`: preserves emoji / CJK


### 4. Message Type Dispatch Matrix

> *Filled by Codex 2026-08-16.*

The BusLoop needs to know **which message types it listens to** and **how to route them**. The matrix below is the per-type decision table.

| Message Type | Sidecar BusLoop? | Dispatch path | Reply type | Notes |
|---|---|---|---|---|
| `TASK_REQUEST` | **Only the orchestrator sidecar** | `Engine.run(task_id, dag)` | `TASK_PROGRESS` (per stage) + `TASK_ACCEPT` (terminal) / `TASK_BLOCKED` (on error) | The only type the orchestrator consumes |
| `TASK_PROGRESS` | **No** (terminal) | n/a (record in eval log only) | n/a | Generated by Engine, observed by requester |
| `TASK_ACCEPT` | **No** (terminal) | n/a | n/a | Generated by Engine on completion |
| `TASK_BLOCKED` | **No** (terminal) | n/a | n/a | Generated by Engine on error |
| `KNOWLEDGE_SHARE` | **Yes** (both sidecars) | Inject into session as context (no Engine) | (optional) `KNOWLEDGE_SHARE` reply | Read-only broadcast; no work triggered |
| `REVIEW_REQUEST` | **Yes** (target's sidecar) | Inject into session, **track as `pending_review_<msg_id>`** | `KNOWLEDGE_SHARE` containing review | Bidirectional: A asks, B answers, A tracks pending |
| `HANDOFF` | **Yes** (target's sidecar) | Inject into session, **carries task context for resumption** | `TASK_ACCEPT` (continue) or `TASK_REQUEST` (re-dispatch new DAG) | Stage A finishes, hands to stage B |
| `DECISION` | **Yes** (target's sidecar) | Inject into session as decision prompt | `DECISION` | Decision-making tasks; reply contains the choice + rationale |

**Implementation rule** (per `BusLoop.__init__`):
```python
WATCH_TO_AGENT: str = "openclaw"  # or "codex"
WATCH_MESSAGE_TYPES: list[str] = [
    MessageType.KNOWLEDGE_SHARE.value,
    MessageType.REVIEW_REQUEST.value,
    MessageType.HANDOFF.value,
    MessageType.DECISION.value,
    MessageType.TASK_REQUEST.value,  # only if this sidecar is the orchestrator
]
```

The **orchestrator sidecar** (only one per host) additionally listens for `TASK_REQUEST` and routes to `Engine.run()`. The **per-agent sidecars** (Codex, OpenClaw) only listen for the conversational / handoff types.

**Default for v0.2**: one sidecar per agent (Codex + OpenClaw), one orchestrator sidecar (any host). Total: 3 sidecars in the dogfood demo.

**Edge case**: a `TASK_REQUEST` sent to `to_agent=codex` (not `to_agent=orchestrator`) is **routed to Codex's session, not the orchestrator**. The sender is expected to use `to_agent=orchestrator` for orchestration; per-agent TASK_REQUESTs are treated as "please handle this directly" (Codex can either accept or re-dispatch).

**Why not one giant matrix per type**: message types have different lifecycles. Some are terminal (Engine-generated, observed only), some are conversational (peer-to-peer), some trigger work. Treating them uniformly confuses the dispatcher.
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

> *Filled by Codex 2026-08-16.*

**D3 unlocks when any of the following is true** (per OpenClaw verify 2026-08-16: `codex app-server daemon` no Windows implementation):

1. **Codex CLI ships Windows daemon lifecycle** — today, `lifecycle is only supported on Unix platforms` is a hard error. Until that error message changes, D3 is permanently blocked on Windows.
2. **Host migrates to Linux / macOS / WSL2** — then the existing daemon works. 30 min host migration, no code change.
3. **Third-party tooling spawns Codex with persistent WS** — e.g., a process manager that wraps `codex exec` and holds a connection open across calls. Effectively a D3 polyfill. No upstream change needed.

**Once D3 unlocks**, this ADR is **superseded by ADR-0013: True Sidecar for Both Agents**. D1 + D2 stay as fallback paths (degraded but functional).

**Alternative paths if D3 is not on roadmap**:

- **D4: `codex --server` mode** — if Codex CLI adds a server flag (HTTP listener + WebSocket). Not on roadmap as of 2026-08-16. Would unblock D3-equivalent.
- **D5: Replace Codex with a daemon-able LLM** — lose the Codex-specific tooling (plan-only / session restore) but gain true autonomy. Trade-off; not recommended unless D3-D4 all blocked.
- **D6: Heavyweight process supervisor** — NSSM / systemd / supervisord wrapping `codex exec`. Possible but fragile.

**Decision criteria for D3 path**:
- If Codex team ships Windows daemon in v0.x of Codex CLI — ADR-0013 supersedes 0012
- If not, by 2026-Q4 reassess D4 / D5
- D6 only as last resort (high operational cost)

**Cost of staying at v0.2 indefinitely**: 1-Enter-per-cycle tax remains. This is **acceptable** for a v0.1/0.2 prototype but **blocks** production deployment where 24/7 autonomous operation is required.

**Monitor**:
- Track `codex app-server daemon` upstream releases (Codex CLI changelog)
- Quarterly re-evaluate D3/D4/D5 based on Codex team roadmap
## Open Questions

- **How does OpenClaw's main session distinguish system-injected bus messages from user input?** (Format must not be confused with user commands)
  - *Codex note*: not my section to answer; deferred to OpenClaw's section 2.
- **Where does `inbox_codex.md` live?** Project-local? Per-Codex-session? User-global?
  - **Codex answer (section 3)**: project-local at `G:/AgentOS/.agentos/inbox_codex.md`. Rationale: bus is project-local (`G:/AgentOS/.agentos/bus.jsonl`), so the inbox follows the same root. Per-Codex-session would fragment context; user-global would couple unrelated projects.
- **Do we need a `last_seen_message_id` per agent to avoid reprocessing? Or rely on `bus.jsonl` since_id filter?**
  - **Codex answer (section 3.1)**: yes, use `codex_last_id.txt` cursor file + `bus.iter_messages(since_id=...)` filter. Justification: bus is append-only, can't "mark" messages as read. Cursor is the only reliable way to know "what's new for me". File-based cursor is simple, atomic (rename), and survives crashes.


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
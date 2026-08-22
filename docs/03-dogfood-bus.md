# Dogfooding the AgentOS Bus

> How Codex and OpenClaw use the AgentOS JSONL bus for actual coordination.
> As of v0.2 (ADR-0012 Accepted), **no human relay is required** for the bus layer.
> The only remaining 1-Enter-per-cycle cost is invoking Codex CLI itself
> (D3 daemon-mode blocked on Windows per ADR-0006 / ADR-0012 section 7).

## Why this matters

The bus is the simplest implementation of A2A Bus (ADR-0001). Before it
existed, 鑰佸ぇ was the human relay: copy-pasting Codex responses to OpenClaw
(ChatGPT) and back. With the bus + `agentos` CLI + ADR-0012 sidecar
BusLoops, both agents write to a shared file. **No human in the loop** for
the bus coordination itself; 鑰佸ぇ only needs to press Enter to invoke
Codex CLI.

This is the bootstrap of AgentOS itself: **we dogfood our own product**
to coordinate the people building it.

## Storage layout

```
G:/AgentOS/.agentos/
  bus.jsonl                            # All A2A messages, append-only, one per line
  inbox_codex.md                       # D1: Codex inbox (drained by bus-watch-codex)
  .openclaw_last_codex_id.txt           # D1: cursor (last consumed message id)
  .codex_last_openclaw_id.txt           # D2: cursor (last consumed by OpenClaw sidecar)
  checkpoints/                         # Orchestrator per-stage checkpoints
  telemetry/
    2026-07-26.jsonl                   # TelemetryHook JSONL events
    2026-08-16.jsonl
```

`bus.jsonl` is the bus. Other paths are state maintained by the sidecar
loop (ADR-0012 D1 / D2) and the orchestrator. All paths are gitignored
(runtime state, not source).

## CLI

| Command | Purpose |
|---|---|
| `agentos send --to X --from Y --text "..."` | Append a text message |
| `agentos send --to X --from Y --from-file reply.md` | Append a file as message payload |
| `agentos send --to X --from Y --text "..." --task t-001` | Tag with task_id for later `show` lookup |
| `agentos receive --to X` | Show messages addressed to X |
| `agentos receive --to X --since <msg-id>` | Only messages after a given ID |
| `agentos show --task t-001` | All messages for a task |
| `agentos search "query"` | Full-text search across messages |
| `agentos inbox` | Total + per-recipient counts |
| `agentos bus-watch-codex` | **D1**: drain new to_codex messages into `inbox_codex.md` (next-turn handler) |
| `agentos bus-poll --to X --cursor PATH` | **D2 helper**: read new messages for an agent (sidecar tool) |

Bus file is configurable via `--bus /custom/path.jsonl` (default `G:/AgentOS/.agentos/bus.jsonl`).

## v0.2 dogfood loop (bus-only)

After ADR-0012, the loop is bus-mediated end-to-end. Codex and OpenClaw
both write to and read from `bus.jsonl`. No human ferry needed.

### Codex → OpenClaw (one cycle)

1. **Codex turn bootstrap**: read any pending bus messages into the
   inbox so they're part of the session's context.
   ```bash
   agentos bus-watch-codex   # one-shot drain, no --watch
   ```
2. **Codex reasons + writes reply** to bus:
   ```bash
   agentos send --to openclaw --from codex --from-file codex_reply.md --task t-001
   ```
3. **Codex exits turn.** OpenClaw sidecar (D2) picks up via cron.

### OpenClaw → Codex (one cycle)

1. **OpenClaw sidecar cron** (every ~30s) injects `[bus-poll]` system event
   into main session.
2. **OpenClaw session** calls `agentos bus-poll --to openclaw --cursor ~/.openclaw/.codex_last_openclaw_id.txt`
   (per ADR-0012 section 2), reads new Codex messages, processes.
3. **OpenClaw session** writes reply to bus:
   ```bash
   agentos send --to codex --from openclaw --text "..." --task t-001
   ```
4. **Next Codex turn** reads via `bus-watch-codex`.

### Net cost to 鑰佸ぇ

- **Before v0.2**: copy-paste full message thread between ChatGPT and Codex every cycle.
- **After v0.2**: press Enter to invoke Codex CLI. OpenClaw sidecar cron does the rest.
- **Long-term (v0.3, D3)**: Codex daemon mode eliminates Enter press entirely (blocked on Windows per ADR-0012 section 7).

## System-injected vs user input distinction

Per ADR-0012 section 2, the three input types to an OpenClaw session
must not be confused:

1. **User input** (鑰佸ぇ typing in webchat / channel): free-form text, **never** `[bus-...]` prefixed.
2. **Cron system events**: always `[bus-<tag>]` prefixed (e.g. `[bus-poll]`).
3. **Memory / tool results**: always arrive via tool-result framing, structurally distinct.

The session rule: "if message starts with `[bus-...]`, treat as bus
trigger; otherwise treat as user input." Worst case (鑰佸ぇ actually types
`[bus-poll]`): session runs poll, finds nothing, replies "no new bus messages".

## Cursor / inbox mechanism (D1 + D2)

Each side maintains its own cursor file:

| Side | Cursor file | Updated when | Tracks messages with `to_agent` |
|---|---|---|---|
| Codex (D1) | `G:/AgentOS/.agentos/.openclaw_last_codex_id.txt` | `agentos bus-watch-codex` runs | `to_agent="codex"` |
| OpenClaw (D2) | `G:/AgentOS/.agentos/.codex_last_openclaw_id.txt` | `agentos bus-poll` runs | `to_agent="openclaw"` |

Cursor update is atomic (temp file + `os.replace`) to avoid partial state
on crash. After successful run, cursor = last consumed message id.

`inbox_codex.md` is the Codex-side Markdown digest of incoming bus
messages, one `## msg-...` block per message. Drained on each
Codex turn start; cleared after processing.

## Bumping to real orchestrator (Plan B)

`Orchestrator Engine` (ADR-0010) reads from the same bus file as the
CLIs and sidecars. When `to_agent="orchestrator"` and
`type="TASK_REQUEST"`, the Engine runs the DAG (Planner + DAGRunner +
Driver dispatch). Replies go back as `TASK_PROGRESS` + `TASK_ACCEPT`.

See `examples/demo_bus_loop.py` for the end-to-end demo.

## What this validates

- A2A message format is sufficient for real cross-agent coordination
- Session keys + task IDs organize multi-turn dialogue
- JSONL is enough for v0.1 / v0.2 (no need for Redis/Kafka yet)
- Cursor + atomic rename works under crash conditions
- System-injected vs user input distinction via `[bus-...]` prefix is safe
- 1-Enter-per-cycle is acceptable for the v0.2 prototype ceiling

## Implementation

- `src/agentos/bus/jsonl.py` — `JSONLBus` class (thread-safe append, filter by recipient / type / since-id, full-text search)
- `src/agentos/bus/watch.py` — `BusWatcher` (file polling for BusLoop integration; v0.2 accepts `message_types` list filter)
- `src/agentos/cli.py` — argparse-based CLI (`agentos` console script; subcommands: send / receive / show / search / inbox / watch / **bus-watch-codex** / **bus-poll**)
- `tests/test_bus.py` + `tests/test_cli.py` + `tests/test_d1_d2_cli.py` — 39 unit tests
- Registered in `pyproject.toml` as `[project.scripts] agentos = "agentos.cli:main"`
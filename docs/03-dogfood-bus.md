# Dogfooding the AgentOS Bus

> How Codex and OpenClaw (via 老大 as relay) use the AgentOS JSONL bus for actual coordination.

## Why this matters

The bus is the simplest implementation of A2A Bus (ADR-0001). Before this existed, 老大 was the human relay: copying Codex responses to OpenClaw (ChatGPT) and vice versa. With the bus, both agents write to a shared file, and 老大 runs simple `agentos` commands to ferry messages.

This is the bootstrap of AgentOS itself: **we dogfood our own product** to coordinate the people building it.

## Storage layout

```
G:/AgentOS/.agentos/
└── bus.jsonl         # All A2A messages, append-only, one per line
```

Each line is a JSON record:

```json
{
  "id": "msg-abc123def456",
  "from_agent": "codex",
  "to_agent": "openclaw",
  "type": "HANDOFF",
  "priority": "NORMAL",
  "payload": {"task_id": "t-001", "text": "..."},
  "created_at": "2026-07-26T12:41:00+00:00",
  "artifact_ref": null
}
```

The shape matches `agentos.schemas.message.Message` plus an optional `artifact_ref` pointer.

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

Bus file is configurable via `--bus /custom/path.jsonl` (default `G:/AgentOS/.agentos/bus.jsonl`).

## Dogfood workflow

The user (老大) still ferries between ChatGPT and Codex (we have no API hook on ChatGPT yet), but the relay is now one CLI command instead of copy-paste between chat windows:

1. **ChatGPT (OpenClaw) answers** -> 老大 runs:
   ```bash
   agentos send --to codex --from openclaw --from-file chatgpt_reply.md --task t-001
   ```

2. **Codex (new turn) reads bus**:
   ```bash
   agentos receive --to codex
   ```
   Then Codex reasons + writes its own response:
   ```bash
   agentos send --to openclaw --from codex --from-file codex_reply.md --task t-001
   ```

3. **老大 reads Codex reply and pastes back into ChatGPT**:
   ```bash
   agentos receive --to openclaw
   ```

## Bumping to real orchestrator (Plan B)

When ADR-0010 Orchestrator Engine ships, the bus layout stays. The orchestrator:

- Polls `bus.jsonl` instead of relying on 老大 to run CLI commands
- Maintains task state, session_keys, checkpoints in the same file
- Optionally moves to a faster queue (Redis Streams) without changing data model

The Bus abstraction (`JSONLBus` class) stays the same; only the persistence layer may swap.

## What this validates

- A2A message format is sufficient for real cross-agent coordination
- Session keys + task IDs organize multi-turn dialogue
- JSONL is enough for v0.1 (no need for Redis/Kafka yet)
- Bus read/write is fast enough that human-in-the-loop does not feel sluggish
- The CLI surface is usable (no need for a GUI yet)

## Implementation

- `src/agentos/bus/jsonl.py` — `JSONLBus` class (thread-safe append, filter by recipient / type / since-id, full-text search)
- `src/agentos/cli.py` — argparse-based CLI (`agentos` console script)
- `tests/test_bus.py` + `tests/test_cli.py` — 21 unit tests
- Registered in `pyproject.toml` as `[project.scripts] agentos = "agentos.cli:main"`
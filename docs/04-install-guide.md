# ADR-0013: Install Guide (v0.2 + v1.0 Production)

> Companion to [ADR-0013 (Codex daemon mode)](0013-codex-daemon-mode.md).
> Covers **install, configure, and run** AgentOS v0.2 (current) and v1.0
> (target launch mid-Nov 2026).

## TL;DR for v0.2 (current)

```bash
git clone https://github.com/increase2002/AgentOS.git
cd AgentOS
pip install -e .[dev]

# 1. Start OpenClaw gateway (if not already running) at 127.0.0.1:18789
# 2. Save gateway token
echo -n "$OPENCLAW_GATEWAY_TOKEN" > G:/AgentOS/.openclaw/gateway.token
chmod 600 G:/AgentOS/.openclaw/gateway.token

# 3. Start OpenClaw D2 sidecar (long-running daemon)
python G:/AgentOS/examples/openclaw_sidecar.py     --interval 5.0     --concurrency 2

# 4. (Each Codex turn) drain new bus messages
agentos bus-watch-codex
```

That's it. The sidecar stays running. Codex turns invoke `bus-watch-codex`,
read `~/.agentos/inbox_codex.md`, process, write reply to bus.

**Cost** (per OpenClaw verify 2026-08-23 12:04 GMT+8):
- ~20k OpenClaw tokens per dispatch
- 5h 60M budget / 20k = ~2400 cycles

## TL;DR for v1.0 (target)

See [ADR-0013](0013-codex-daemon-mode.md) for the constraint:
**v1.0 production deployment requires Linux/macOS/WSL2** (Windows permanent blocked
on Codex daemon). Single-host interactive mode still works on Windows
with 1-Enter-per-cycle cost.

Full v1.0 install (target) once OpenClaw sidecar gains the Codex-spawn
dispatcher (OpenClaw is implementing):

```bash
# On Linux/macOS/WSL2 + Codex daemon running:
codex app-server daemon start

# Start OpenClaw sidecar with Codex dispatcher
python examples/openclaw_sidecar.py --interval 5.0 --dispatcher both
# "both" = openclaw_main_session (Real OpenClaw LLM)
#       + codex_subprocess (spawns `codex exec --json`)
```

Zero-touch loop active. Codex and OpenClaw mutually wake each other via bus.

## Requirements (v0.2 and v1.0)

### v0.2 (current)

| Requirement | Min version | Notes |
|---|---|---|
| Python | 3.11+ | tested on 3.13 |
| Git | 2.40+ | for repo operations |
| OpenClaw gateway | 2026.7.1-2+ | Contract B endpoint (HTTP `/v1/chat/completions`) |
| Codex CLI | 0.144.1+ | `codex exec --json` mode (not required for v0.2 demo, only v1.0) |
| 5h token budget | 60M combined | shared between Codex + OpenClaw |

### v1.0 (target)

Everything in v0.2, plus:
| Requirement | Notes |
|---|---|
| Linux / macOS / **WSL2** | Windows single-host only (1-Enter cost) |
| Codex app-server daemon | `codex app-server daemon start` (Unix only as of 2026-08) |
| Token-aware rate limit | per-agent token bucket, ADR-0013 §B |
| Bus ACL | agent token + per-agent write permission |

## Configuration

### Token storage

Default token location: `G:/AgentOS/.openclaw/gateway.token` (gitignored).

Override path via env var: `AGENTOS_GATEWAY_TOKEN` (preferred for prod/CI).

Token resolution order (per `agentos.memory.openclaw_token.resolve_openclaw_token`):
1. `Path.home() / ".openclaw" / "openclaw.json"` -> `gateway.auth.token`
2. Fallback: `G:/AgentOS/.openclaw/gateway.token`

### Bus file

Default: `G:/AgentOS/.agentos/bus.jsonl` (gitignored).

Override: `--bus <path>` on every `agentos` CLI invocation, or set `AGENTOS_BUS` env var.

### Telemetry

Default output: `G:/AgentOS/telemetry/{YYYY-MM-DD}.jsonl`.

Disable via env: `AGENTOS_TELEMETRY=off`.

### Driver config

Each driver takes its config via constructor kwargs. See
`agentos/driver/openclaw_driver.py::OpenClawDriver.__init__` for example.

## Validation

After install, run:

```bash
pytest                          # all tests (target: 273+ passing)
python examples/d1_demo.py      # D1+D2 closed-loop mock
python examples/memory_service_demo.py  # MemoryService fan-out
```

For real LLM (costs ~20k tokens):

```bash
python examples/c1_real_e2e.py  # MemoryService + real OpenClaw Contract B
```

## Running D2 Sidecar in production

```bash
nohup python examples/openclaw_sidecar.py     --interval 5     --concurrency 2     --bus /var/lib/agentos/bus.jsonl     > /var/log/agentos/sidecar.log 2>&1 &

# To check status:
agentos inbox
agentos show --task <task-id>
```

Sidecar auto-rotates cursor file (atomic rename per ADR-0012 §3.1).

## Known limitations (v0.2 → v1.0)

| Limitation | Workaround | v1.0 fix |
|---|---|---|
| Windows host needs 1-Enter/cycle | accept interactive mode | Codex daemon on Linux |
| OpenClaw sidecar doesn't spawn Codex subprocess yet | user invokes Codex manually | OpenClaw dispatcher (in progress) |
| No bus-write ACL | trust local bus | agent token + ACL |
| No rate limiting | careful token budgeting | token bucket per ADR-0013 §B |
| Bus has no schema version field | bus is internal-only | add `schema_version` |

## Troubleshooting

| Symptom | Check |
|---|---|
| `agentos bus-poll` returns 0 messages | check `~/.agentos/bus.jsonl` exists + `to_agent=openclaw` |
| OpenClaw driver health_check=False | check gateway at `http://127.0.0.1:18789/health` |
| `install_telemetry` no-op | check `AGENTOS_TELEMETRY` env not "off" |
| Cursor stuck (idempotent re-read returns empty) | check cursor file path matches driver |

## References

- ADR-0001 (Integration Method): base bus + driver contract
- ADR-0010 (Orchestrator Engine): bus-driven DAG execution
- ADR-0012 (Sidecar BusLoops + Agent Autonomy): the architecture this guide implements
- ADR-0013 (Codex daemon mode): the v1.0 host constraint

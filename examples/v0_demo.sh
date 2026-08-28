#!/usr/bin/env bash
# v0_demo.sh - AgentOS v0.2 end-to-end demo (one command, no LLM cost by default).
#
# Demonstrates the closed-loop bus-mediated agent collaboration:
#   bus-write (Codex) -> bus-poll (OpenClaw sidecar) -> bus-reply (OpenClaw) -> bus-watch-codex (Codex)
#
# Modes:
#   default (--dry-run): uses FakeOCDriver for OpenClaw, no real LLM cost
#   --real: spawns actual OpenClaw sidecar (requires gateway running + LLM budget)
#
# Usage:
#   bash examples/v0_demo.sh                    # dry-run demo (no LLM)
#   bash examples/v0_demo.sh --real             # real OpenClaw LLM call
#   bash examples/v0_demo.sh --real --poll-interval 1  # custom poll

set -e

REAL="${1:-}"
echo "================================================================"
echo "AgentOS v0.2 end-to-end demo"
if [ "$REAL" = "--real" ]; then
    echo "Mode: REAL (spawns OpenClaw sidecar, costs LLM tokens)"
else
    echo "Mode: DRY-RUN (mock drivers, no LLM cost)"
fi
echo "================================================================"
echo

# Resolve paths
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEST_BUS_DIR="$(mktemp -d)"
export AGENTOS_BUS="$TEST_BUS_DIR/bus.jsonl"
export AGENTOS_CURSOR="$TEST_BUS_DIR/codex_cursor.txt"
export AGENTOS_INBOX="$TEST_BUS_DIR/inbox_codex.md"

cleanup() {
    echo
    echo "Cleaning up temp bus dir: $TEST_BUS_DIR"
    rm -rf "$TEST_BUS_DIR"
}
trap cleanup EXIT

if [ "$REAL" = "--real" ]; then
    # Real mode: need OpenClaw gateway running at 127.0.0.1:18789
    if ! curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:18789/health" | grep -q 200; then
        echo "ERROR: OpenClaw gateway not reachable at 127.0.0.1:18789"
        echo "  Start OpenClaw first, or run without --real for a dry-run."
        exit 1
    fi

    echo "[1/5] Starting OpenClaw sidecar (long-running daemon)"
    SIDECAR_PID_FILE="$TEST_BUS_DIR/sidecar.pid"
    nohup "$SCRIPT_DIR/openclaw_sidecar.py"         --bus "$AGENTOS_BUS"         > "$TEST_BUS_DIR/sidecar.log" 2>&1 &
    echo $! > "$SIDECAR_PID_FILE"
    sleep 2  # let sidecar initialize

    cleanup_with_sidecar() {
        if [ -f "$SIDECAR_PID_FILE" ]; then
            kill "$(cat "$SIDECAR_PID_FILE")" 2>/dev/null || true
        fi
        cleanup
    }
    trap cleanup_with_sidecar EXIT

    echo "       sidecar PID: $(cat $SIDECAR_PID_FILE)"
    echo

    echo "[2/5] Codex sends a task to OpenClaw via bus"
    "$ROOT_DIR/.venv/Scripts/agentos.exe" send         --to openclaw         --from codex         --text "ADR-0012 done. demo v0_demo.sh running. what is the W1 ship gate status?"         --task v0-demo
    echo

    echo "[3/5] Sleeping 8s for sidecar to poll + reply (real OpenClaw LLM call)"
    sleep 8
else
    # Dry-run mode: use the in-process bus + d1_demo style
    echo "[1/5] Using dry-run demo (no sidecar needed)"
    python -c "
import sys
sys.path.insert(0, r'$ROOT_DIR/src')
sys.path.insert(0, r'$ROOT_DIR/examples')
# Mock sidecar behavior: openclaw_sidecar.py --dry-run
from agentos import cli
from agentos.bus.jsonl import JSONLBus
from pathlib import Path
import json

# Step 1: Codex sends a message
cli.main(['--bus', r'$AGENTOS_BUS', 'send',
         '--to', 'openclaw', '--from', 'codex',
         '--text', 'ADR-0012 done. demo v0_demo.sh running. what is W1 ship gate?',
         '--task', 'v0-demo'])

# Step 2: OpenClaw 'sidecar' polls (mock reply)
print('       [mock sidecar] polling bus...')
bus = JSONLBus(r'$AGENTOS_BUS')
msgs = bus.to_agent('openclaw')
print(f'       [mock sidecar] found {len(msgs)} message(s)')

# Step 3: Simulate OpenClaw reply
print('       [mock sidecar] drafting reply...')
reply_text = 'W1 ship gate = OpenClaw sidecar spawns Codex CLI subprocess (per ADR-0013).'
cli.main(['--bus', r'$AGENTOS_BUS', 'send',
         '--to', 'codex', '--from', 'openclaw',
         '--text', reply_text,
         '--task', 'v0-demo'])

# Step 4: Codex drains bus
print('       [codex] draining bus into inbox...')
cli.main(['--bus', r'$AGENTOS_BUS', 'bus-watch-codex',
         '--inbox', r'$AGENTOS_INBOX',
         '--cursor', r'$AGENTOS_CURSOR'])

# Show inbox
print()
print('Inbox contents:')
print('-' * 60)
inbox_text = Path(r'$AGENTOS_INBOX').read_text(encoding='utf-8')
for line in inbox_text.splitlines():
    if line.strip():
        print(line)
print('-' * 60)
"
    echo
fi

if [ "$REAL" = "--real" ]; then
    echo "[4/5] Codex drains bus via bus-watch-codex"
    "$ROOT_DIR/.venv/Scripts/agentos.exe" bus-watch-codex         --bus "$AGENTOS_BUS"         --inbox "$AGENTOS_INBOX"         --cursor "$AGENTOS_CURSOR"
    echo

    echo "[5/5] Inbox contents (real reply):"
    echo "================================================================"
    cat "$AGENTOS_INBOX"
    echo "================================================================"
fi

echo
echo "================================================================"
echo "Demo complete."
if [ "$REAL" = "--real" ]; then
    echo "Real LLM cost: ~20k tokens (1 OpenClaw dispatch)"
else
    echo "Token cost: 0 (dry-run)"
fi
echo "Bus messages: $(wc -l < "$AGENTOS_BUS")"
echo "================================================================"

"""D1 + D2 closed-loop demo (no real LLM calls, mock-only).

Demonstrates the bus-watch-codex (D1) and bus-poll (D2) CLI subcommands
in a closed loop, WITHOUT making real LLM calls. Uses the JSONLBus API
directly to simulate the bus, so the example runs in <1s with no
network or LLM quota cost.

Real LLM integration: see examples/c1_real_e2e.py.

Usage:
    python examples/d1_demo.py

Expected output:
    [1] Codex sends a message to OpenClaw
    [2] OpenClaw polls bus, sees Codex message
    [3] OpenClaw replies on the bus
    [4] Codex drains bus, sees OpenClaw reply (via bus-watch-codex)
    [5] cursor advances correctly (no double-process)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agentos import cli
from agentos.bus.jsonl import JSONLBus


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        bus = td_path / "bus.jsonl"
        cursor = td_path / "codex_last_id.txt"
        inbox = td_path / "inbox_codex.md"

        # 1) Codex sends a message to OpenClaw
        print("[1] Codex sends message to OpenClaw")
        rc = cli.main([
            "--bus", str(bus),
            "send", "--to", "openclaw", "--from", "codex", "--text",
            "ADR-0012 done. what's next?", "--task", "t-d1-demo",
        ])
        assert rc == 0

        # 2) OpenClaw-side: bus-poll
        print("[2] OpenClaw polls bus (bus-poll)")
        rc = cli.main([
            "--bus", str(bus),
            "bus-poll", "--to", "openclaw",
            "--cursor", str(td_path / "openclaw_last_id.txt"),
        ])
        assert rc == 0

        # 3) OpenClaw replies on the bus
        print("[3] OpenClaw sends reply")
        rc = cli.main([
            "--bus", str(bus),
            "send", "--to", "codex", "--from", "openclaw", "--text",
            "D2 patch is ready. merging now.", "--task", "t-d1-demo",
        ])
        assert rc == 0

        # 4) Codex-side: bus-watch-codex drains new messages into inbox
        print("[4] Codex drains bus (bus-watch-codex) into inbox")
        rc = cli.main([
            "--bus", str(bus),
            "bus-watch-codex", "--inbox", str(inbox),
            "--cursor", str(cursor),
        ])
        assert rc == 0

        # 5) Verify cursor advanced and inbox has the reply
        cursor_id = cursor.read_text(encoding="utf-8").strip()
        assert cursor_id.startswith("msg-"), f"bad cursor: {cursor_id!r}"
        inbox_content = inbox.read_text(encoding="utf-8")
        assert "D2 patch is ready" in inbox_content, "reply not in inbox"
        assert "what's next" not in inbox_content, "old message should not reappear"
        print(f"[5] cursor={cursor_id} inbox OK ({len(inbox_content)} bytes)")

        # 6) Second watch: cursor prevents re-reading same messages
        print("[6] Second bus-watch-codex (idempotent)")
        inbox.unlink()
        rc = cli.main([
            "--bus", str(bus),
            "bus-watch-codex", "--inbox", str(inbox),
            "--cursor", str(cursor),
        ])
        assert rc == 0
        assert not inbox.exists(), "inbox should NOT be recreated (cursor blocks)"
        print(f"[6] idempotent: cursor={cursor_id} blocks re-read")

        # 7) Show all 4 messages in bus
        print("[7] Bus contents:")
        jsonl = JSONLBus(bus)
        msgs = list(jsonl.iter_messages())
        for m in msgs:
            print(f"    {m['id'][:12]} {m['from_agent']:8s} -> {m['to_agent']:8s} "
                  f"{m['type']:14s}: {m.get('payload', {}).get('text', '')[:40]}")

        print()
        print("D1 + D2 closed loop verified (mock, no LLM).")


if __name__ == "__main__":
    main()

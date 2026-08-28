"""D2 sidecar cursor / bus-poll tests.

The dispatcher routing, dry-run, error containment, and semaphore
gating tests live in test_sidecar_dispatcher.py (Codex, a31c4e9) and
the rate-limit gate tests live in test_openclaw_sidecar.py. This
file keeps the original D2 sidecar contract tests for the
JSONLBus cursor + replay path that BusWatcher relies on.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentos.bus.jsonl import JSONLBus  # noqa: E402
from agentos.schemas.message import Message, MessageType, Priority  # noqa: E402


def _make_msg(from_="codex", payload=None, msg_id="msg-test-1"):
    return Message(
        id=msg_id, from_agent=from_, to_agent="openclaw",
        type=MessageType.HANDOFF, priority=Priority.NORMAL,
        payload=payload or {},
    )


# cursor management

def test_cursor_idempotent_reread(tmp_path):
    bus = JSONLBus(tmp_path / "bus.jsonl")
    cur = tmp_path / "openclaw_last_id.txt"
    bus.append(_make_msg(msg_id="msg-A", payload={"text": "1"}))
    bus.append(_make_msg(msg_id="msg-B", payload={"text": "2"}))
    cid = cur.read_text().strip() if cur.exists() else None
    msgs1 = bus.to_agent("openclaw", since_id=cid)
    assert len(msgs1) == 2
    cur.write_text(msgs1[-1]["id"], encoding="utf-8")
    msgs2 = bus.to_agent("openclaw", since_id=cur.read_text().strip())
    assert msgs2 == []


def test_cursor_recovers_partial(tmp_path):
    bus = JSONLBus(tmp_path / "bus.jsonl")
    cur = tmp_path / "openclaw_last_id.txt"
    for i in (1, 2, 3):
        bus.append(_make_msg(msg_id=f"msg-{i}", payload={"text": str(i)}))
    cur.write_text("msg-1", encoding="utf-8")
    msgs = bus.to_agent("openclaw", since_id="msg-1")
    assert [m["id"] for m in msgs] == ["msg-2", "msg-3"]
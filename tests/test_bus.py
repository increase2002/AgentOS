"""Tests for the JSONL message bus."""

from __future__ import annotations

import json
import uuid

import pytest

from agentos.bus.jsonl import JSONLBus
from agentos.schemas.message import Message, MessageType, Priority


@pytest.fixture
def bus(tmp_path):
    return JSONLBus(tmp_path / "bus.jsonl")


def _msg(
    from_agent: str = "codex",
    to_agent: str = "openclaw",
    msg_type: MessageType = MessageType.HANDOFF,
    payload: dict | None = None,
) -> Message:
    return Message(
        id=f"msg-{uuid.uuid4().hex[:8]}",
        from_agent=from_agent,
        to_agent=to_agent,
        type=msg_type,
        priority=Priority.NORMAL,
        payload=payload or {"text": "hello"},
    )


def test_append_creates_file(tmp_path):
    path = tmp_path / "bus.jsonl"
    assert not path.exists()
    JSONLBus(path)
    assert path.exists()


def test_append_and_count(bus):
    bus.append(_msg())
    bus.append(_msg())
    assert bus.count() == 2


def test_to_agent_filter(bus):
    bus.append(_msg(from_agent="codex", to_agent="openclaw"))
    bus.append(_msg(from_agent="openclaw", to_agent="codex"))
    codex_inbox = bus.to_agent("codex")
    assert len(codex_inbox) == 1
    assert codex_inbox[0]["from_agent"] == "openclaw"


def test_iter_messages_with_type_filter(bus):
    bus.append(_msg(msg_type=MessageType.TASK_REQUEST))
    bus.append(_msg(msg_type=MessageType.HANDOFF))
    matches = list(bus.iter_messages(message_type=MessageType.HANDOFF))
    assert len(matches) == 1


def test_since_id_filter(bus):
    bus.append(_msg())
    first = bus.to_agent("openclaw")
    first_id = first[0]["id"]
    bus.append(_msg())
    new_msgs = bus.to_agent("openclaw", since_id=first_id)
    assert len(new_msgs) == 1
    assert new_msgs[0]["id"] != first_id


def test_search_query(bus):
    bus.append(_msg(payload={"text": "tool_subset enforcement"}))
    bus.append(_msg(payload={"text": "memory federation"}))
    matches = bus.search("tool_subset")
    assert len(matches) == 1


def test_search_includes_artifact_ref(bus):
    bus.append(_msg(payload={"file": "/tmp/abc.md"}))
    matches = bus.search("abc.md")
    assert len(matches) == 1


def test_clear(bus):
    bus.append(_msg())
    bus.append(_msg())
    bus.clear()
    assert bus.count() == 0


def test_corrupt_line_skipped(bus):
    bus.path.write_text(
        "garbage line\n" + json.dumps({"id": "valid-rec"}) + "\n",
        encoding="utf-8",
    )
    msgs = list(bus.iter_messages())
    assert any(m.get("id") == "valid-rec" for m in msgs)


def test_summary_counts(bus):
    bus.append(_msg(from_agent="a", to_agent="codex"))
    bus.append(_msg(from_agent="b", to_agent="codex"))
    bus.append(_msg(from_agent="c", to_agent="openclaw"))
    s = bus.summary()
    assert s == {"codex": 2, "openclaw": 1}


def test_message_round_trip_preserves_payload(bus):
    original = _msg(payload={"task_id": "t-001", "decisions": ["ADR-0009", "ADR-0011"]})
    bus.append(original, artifact_ref="/tmp/notes.md")
    msgs = bus.to_agent("openclaw")
    assert len(msgs) == 1
    rec = msgs[0]
    assert rec["payload"]["task_id"] == "t-001"
    assert rec["payload"]["decisions"] == ["ADR-0009", "ADR-0011"]
    assert rec["artifact_ref"] == "/tmp/notes.md"


def test_thread_safe_append(bus):
    import threading
    errors: list[Exception] = []

    def worker():
        try:
            for _ in range(20):
                bus.append(_msg())
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert bus.count() == 80  # 4 threads * 20 messages
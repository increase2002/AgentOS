"""Tests for ADR-0012 parameterised BusWatcher message_types filter.

These tests pin the v0.2 contract:
- Single legacy ``message_type`` still works (back-compat).
- New ``message_types`` list filters by set membership.
- ``None`` (default) means no type filter — watcher passes everything
  that matches the other filters.

Ref: ADR-0012 section 1 (Sidecar BusLoop Abstraction).
"""
from __future__ import annotations

import json
from pathlib import Path

from agentos.bus.watch import BusWatcher
from agentos.schemas.message import Message, MessageType


def _append(bus: Path, msg: Message) -> None:
    bus.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "id": msg.id,
        "from_agent": msg.from_agent,
        "to_agent": msg.to_agent,
        "type": msg.type.value,
        "priority": msg.priority.value,
        "payload": msg.payload,
        "created_at": msg.created_at.isoformat(),
    }
    with bus.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _make(msg_type: MessageType, to: str = "openclaw", from_: str = "codex") -> Message:
    return Message(
        id=f"msg-{msg_type.value[:4]}-test",
        from_agent=from_,
        to_agent=to,
        type=msg_type,
        payload={"hello": "world"},
    )


def test_legacy_message_type_singular_still_works(tmp_path: Path) -> None:
    bus = tmp_path / "bus.jsonl"
    captured: list[Message] = []
    watcher = BusWatcher(
        bus, captured.append,
        to_agent="openclaw",
        message_type=MessageType.TASK_REQUEST.value,
        poll_interval_s=0.01,
        from_start=True,
    )
    _append(bus, _make(MessageType.TASK_REQUEST))
    _append(bus, _make(MessageType.KNOWLEDGE_SHARE))
    watcher._poll_once()  # type: ignore[attr-defined]
    assert [m.id for m in captured] == ["msg-TASK-test"], captured
    assert watcher.message_types == [MessageType.TASK_REQUEST.value]


def test_message_types_list_filters_by_membership(tmp_path: Path) -> None:
    bus = tmp_path / "bus.jsonl"
    captured: list[Message] = []
    watcher = BusWatcher(
        bus, captured.append,
        to_agent="openclaw",
        message_types=[MessageType.KNOWLEDGE_SHARE.value,
                       MessageType.REVIEW_REQUEST.value],
        poll_interval_s=0.01,
        from_start=True,
    )
    _append(bus, _make(MessageType.TASK_REQUEST))          # filtered out
    _append(bus, _make(MessageType.KNOWLEDGE_SHARE))       # captured
    _append(bus, _make(MessageType.TASK_PROGRESS))         # filtered out
    _append(bus, _make(MessageType.REVIEW_REQUEST))        # captured
    watcher._poll_once()  # type: ignore[attr-defined]
    types = [m.type.value for m in captured]
    assert types == ["KNOWLEDGE_SHARE", "REVIEW_REQUEST"], types


def test_message_types_none_means_no_type_filter(tmp_path: Path) -> None:
    bus = tmp_path / "bus.jsonl"
    captured: list[Message] = []
    watcher = BusWatcher(
        bus, captured.append,
        to_agent="openclaw",
        message_types=None,
        poll_interval_s=0.01,
        from_start=True,
    )
    _append(bus, _make(MessageType.TASK_REQUEST))
    _append(bus, _make(MessageType.HANDOFF))
    _append(bus, _make(MessageType.DECISION))
    watcher._poll_once()  # type: ignore[attr-defined]
    assert [m.type.value for m in captured] == ["TASK_REQUEST", "HANDOFF", "DECISION"]


def test_message_types_takes_precedence_over_message_type(tmp_path: Path) -> None:
    """If both are passed, message_types wins (newer API overrides legacy)."""
    bus = tmp_path / "bus.jsonl"
    captured: list[Message] = []
    watcher = BusWatcher(
        bus, captured.append,
        to_agent="openclaw",
        message_type=MessageType.TASK_REQUEST.value,  # legacy
        message_types=[MessageType.HANDOFF.value],     # new — wins
        poll_interval_s=0.01,
        from_start=True,
    )
    _append(bus, _make(MessageType.TASK_REQUEST))
    _append(bus, _make(MessageType.HANDOFF))
    watcher._poll_once()  # type: ignore[attr-defined]
    assert [m.type.value for m in captured] == ["HANDOFF"]
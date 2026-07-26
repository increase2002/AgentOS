"""Tests for BusWatcher (file bridge for tailing bus.jsonl)."""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentos.bus.jsonl import JSONLBus
from agentos.bus.watch import BusWatcher
from agentos.schemas.message import Message, MessageType, Priority


def _make_msg(
    *,
    to: str = "openclaw",
    sender: str = "codex",
    type_: MessageType = MessageType.HANDOFF,
    text: str = "hello",
) -> Message:
    return Message(
        id=f"msg-{uuid.uuid4().hex[:12]}",
        from_agent=sender,
        to_agent=to,
        type=type_,
        priority=Priority.NORMAL,
        payload={"text": text},
    )


def test_watcher_fires_on_new_message(tmp_path: Path):
    bus_path = tmp_path / "bus.jsonl"
    received: list[Message] = []
    received_event = threading.Event()

    def handler(msg: Message) -> None:
        received.append(msg)
        received_event.set()

    watcher = BusWatcher(bus_path, handler, to_agent="openclaw", poll_interval_s=0.05)
    t = threading.Thread(target=watcher.watch, daemon=True)
    t.start()
    try:
        time.sleep(0.15)  # let watcher init
        JSONLBus(bus_path).append(_make_msg(text="ping"))
        assert received_event.wait(timeout=2.0), "watcher did not fire within 2s"
        assert received[0].payload["text"] == "ping"
    finally:
        watcher.stop()
        t.join(timeout=1.0)


def test_watcher_respects_to_filter(tmp_path: Path):
    bus_path = tmp_path / "bus.jsonl"
    received: list[Message] = []
    received_event = threading.Event()

    def handler(msg: Message) -> None:
        received.append(msg)
        received_event.set()

    watcher = BusWatcher(bus_path, handler, to_agent="openclaw", poll_interval_s=0.05)
    t = threading.Thread(target=watcher.watch, daemon=True)
    t.start()
    try:
        time.sleep(0.15)
        # Send to a different agent — should NOT trigger.
        JSONLBus(bus_path).append(_make_msg(to="someone-else", text="for someone else"))
        time.sleep(0.3)
        assert not received, f"received unexpected: {received}"
        # Now send to openclaw — should trigger.
        JSONLBus(bus_path).append(_make_msg(text="for openclaw"))
        assert received_event.wait(timeout=2.0)
    finally:
        watcher.stop()
        t.join(timeout=1.0)


def test_watcher_respects_type_filter(tmp_path: Path):
    bus_path = tmp_path / "bus.jsonl"
    received: list[Message] = []
    received_event = threading.Event()

    def handler(msg: Message) -> None:
        received.append(msg)
        received_event.set()

    watcher = BusWatcher(
        bus_path, handler, to_agent="openclaw",
        message_type=MessageType.KNOWLEDGE_SHARE.value, poll_interval_s=0.05,
    )
    t = threading.Thread(target=watcher.watch, daemon=True)
    t.start()
    try:
        time.sleep(0.15)
        JSONLBus(bus_path).append(_make_msg(type_=MessageType.HANDOFF, text="h"))
        time.sleep(0.3)
        assert not received, f"received unexpected: {received}"
        JSONLBus(bus_path).append(_make_msg(type_=MessageType.KNOWLEDGE_SHARE, text="k"))
        assert received_event.wait(timeout=2.0)
    finally:
        watcher.stop()
        t.join(timeout=1.0)


def test_watcher_handles_multiple_messages(tmp_path: Path):
    bus_path = tmp_path / "bus.jsonl"
    received: list[Message] = []
    received_event = threading.Event()
    expected_count = 5

    def handler(msg: Message) -> None:
        received.append(msg)
        if len(received) >= expected_count:
            received_event.set()

    watcher = BusWatcher(bus_path, handler, to_agent="openclaw", poll_interval_s=0.05)
    t = threading.Thread(target=watcher.watch, daemon=True)
    t.start()
    try:
        time.sleep(0.15)
        bus = JSONLBus(bus_path)
        for i in range(expected_count):
            bus.append(_make_msg(text=f"msg-{i}"))
        assert received_event.wait(timeout=3.0), f"only got {len(received)}/{expected_count}"
        texts = [m.payload["text"] for m in received]
        assert texts == [f"msg-{i}" for i in range(expected_count)]
    finally:
        watcher.stop()
        t.join(timeout=1.0)


def test_watcher_handles_handler_exception(tmp_path: Path):
    bus_path = tmp_path / "bus.jsonl"
    received: list[Message] = []
    good_event = threading.Event()

    def handler(msg: Message) -> None:
        received.append(msg)
        if len(received) == 1:
            raise RuntimeError("handler kaboom")
        if len(received) >= 2:
            good_event.set()

    watcher = BusWatcher(bus_path, handler, to_agent="openclaw", poll_interval_s=0.05)
    t = threading.Thread(target=watcher.watch, daemon=True)
    t.start()
    try:
        time.sleep(0.15)
        bus = JSONLBus(bus_path)
        bus.append(_make_msg(text="boom"))
        time.sleep(0.3)
        bus.append(_make_msg(text="still alive"))
        assert good_event.wait(timeout=2.0), "watcher died after handler exception"
        assert len(received) == 2
    finally:
        watcher.stop()
        t.join(timeout=1.0)


def test_watcher_dedup_on_rotation(tmp_path: Path):
    """If the bus file is truncated, watcher should re-read from start."""
    bus_path = tmp_path / "bus.jsonl"
    received: list[Message] = []
    received_event = threading.Event()

    def handler(msg: Message) -> None:
        received.append(msg)
        received_event.set()

    # Pre-populate file with one message.
    JSONLBus(bus_path).append(_make_msg(text="pre-existing"))

    watcher = BusWatcher(bus_path, handler, to_agent="openclaw", poll_interval_s=0.05)
    t = threading.Thread(target=watcher.watch, daemon=True)
    t.start()
    try:
        time.sleep(0.15)
        # Force rotation: rewrite the file with two new messages.
        bus_path.write_text("", encoding="utf-8")  # simulate truncation
        time.sleep(0.3)
        bus = JSONLBus(bus_path)
        bus.append(_make_msg(text="after-rotation-1"))
        bus.append(_make_msg(text="after-rotation-2"))
        # Watcher should now see the new ones (and possibly re-see the pre-existing one
        # if de-dup did not hold; both behaviours are acceptable as long as the new
        # messages arrive).
        time.sleep(0.5)
        texts = [m.payload["text"] for m in received]
        assert "after-rotation-1" in texts
        assert "after-rotation-2" in texts
    finally:
        watcher.stop()
        t.join(timeout=1.0)


def test_watcher_stop_is_idempotent(tmp_path: Path):
    bus_path = tmp_path / "bus.jsonl"
    watcher = BusWatcher(bus_path, lambda m: None, poll_interval_s=0.05)
    t = threading.Thread(target=watcher.watch, daemon=True)
    t.start()
    time.sleep(0.1)
    watcher.stop()
    watcher.stop()  # idempotent
    watcher.stop()
    t.join(timeout=1.0)


def test_watcher_waits_for_bus_file(tmp_path: Path):
    """Watcher should wait patiently if bus file does not exist yet."""
    bus_path = tmp_path / "not_yet.jsonl"
    received: list[Message] = []
    received_event = threading.Event()

    def handler(msg: Message) -> None:
        received.append(msg)
        received_event.set()

    watcher = BusWatcher(bus_path, handler, to_agent="openclaw", poll_interval_s=0.05)
    t = threading.Thread(target=watcher.watch, daemon=True)
    t.start()
    try:
        time.sleep(0.15)
        # File does not exist yet — watcher should still be alive.
        assert t.is_alive()
        # Create the bus and append — should fire.
        bus_path.parent.mkdir(parents=True, exist_ok=True)
        JSONLBus(bus_path).append(_make_msg(text="late but ok"))
        assert received_event.wait(timeout=2.0)
    finally:
        watcher.stop()
        t.join(timeout=1.0)


def test_watcher_from_start_replays_history(tmp_path: Path):
    """With from_start=True, watcher should see pre-existing tail."""
    bus_path = tmp_path / "bus.jsonl"
    # Pre-populate with 2 messages.
    bus = JSONLBus(bus_path)
    bus.append(_make_msg(text="history-1"))
    bus.append(_make_msg(text="history-2"))

    received: list[Message] = []
    received_event = threading.Event()

    def handler(msg: Message) -> None:
        received.append(msg)
        if len(received) >= 2:
            received_event.set()

    watcher = BusWatcher(
        bus_path, handler, to_agent="openclaw",
        poll_interval_s=0.05, from_start=True,
    )
    t = threading.Thread(target=watcher.watch, daemon=True)
    t.start()
    try:
        assert received_event.wait(timeout=2.0), f"only got {len(received)} messages"
        texts = [m.payload["text"] for m in received]
        assert "history-1" in texts
        assert "history-2" in texts
    finally:
        watcher.stop()
        t.join(timeout=1.0)


def test_watcher_skips_history_by_default(tmp_path: Path):
    """Without from_start, watcher should skip pre-existing tail."""
    bus_path = tmp_path / "bus.jsonl"
    bus = JSONLBus(bus_path)
    bus.append(_make_msg(text="old"))

    received: list[Message] = []
    new_event = threading.Event()

    def handler(msg: Message) -> None:
        received.append(msg)
        if msg.payload["text"] == "new":
            new_event.set()

    watcher = BusWatcher(bus_path, handler, to_agent="openclaw", poll_interval_s=0.05)
    t = threading.Thread(target=watcher.watch, daemon=True)
    t.start()
    try:
        time.sleep(0.3)  # let watcher init + skip "old"
        bus.append(_make_msg(text="new"))
        assert new_event.wait(timeout=2.0)
        assert len(received) == 1
        assert received[0].payload["text"] == "new"
    finally:
        watcher.stop()
        t.join(timeout=1.0)
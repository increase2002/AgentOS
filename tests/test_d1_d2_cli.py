"""
Tests for the bus-watch-codex and bus-poll CLI subcommands (D1/D2 helpers).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from agentos import cli


def _send(bus_path, *, to="codex", from_="openclaw", msg_type="HANDOFF",
          text="hello codex", task_id=None):
    """Helper: append a message to the test bus."""
    bus_path.parent.mkdir(parents=True, exist_ok=True)
    if not bus_path.exists():
        bus_path.touch()
    msg = {
        "id": f"msg-{bus_path.stat().st_size + 1:04d}",
        "from_agent": from_,
        "to_agent": to,
        "type": msg_type,
        "priority": "NORMAL",
        "payload": {"text": text, **({"task_id": task_id} if task_id else {})},
        "created_at": "2026-08-22T10:00:00+00:00",
        "artifact_ref": None,
    }
    with bus_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(msg) + chr(10))
    return msg


def test_bus_watch_codex_no_messages(tmp_path):
    bus = tmp_path / "bus.jsonl"
    inbox = tmp_path / "inbox.md"
    cursor = tmp_path / "cursor.txt"
    rc = cli.main(["--bus", str(bus), "bus-watch-codex", "--inbox", str(inbox), "--cursor", str(cursor)])
    assert rc == 0
    assert not inbox.exists()


def test_bus_watch_codex_drains_and_advances_cursor(tmp_path):
    bus = tmp_path / "bus.jsonl"
    inbox = tmp_path / "inbox.md"
    cursor = tmp_path / "cursor.txt"
    _send(bus, text="first message")
    _send(bus, text="second message")
    rc = cli.main(["--bus", str(bus), "bus-watch-codex", "--inbox", str(inbox), "--cursor", str(cursor)])
    assert rc == 0
    assert inbox.exists()
    content = inbox.read_text(encoding="utf-8")
    assert "first message" in content
    assert "second message" in content
    cursor_text = cursor.read_text(encoding="utf-8")
    assert cursor_text.startswith("msg-") and len(cursor_text) > 4


def test_bus_watch_codex_idempotent(tmp_path):
    bus = tmp_path / "bus.jsonl"
    inbox = tmp_path / "inbox.md"
    cursor = tmp_path / "cursor.txt"
    _send(bus, text="only message")
    cli.main(["--bus", str(bus), "bus-watch-codex", "--inbox", str(inbox), "--cursor", str(cursor)])
    inbox.unlink()
    rc = cli.main(["--bus", str(bus), "bus-watch-codex", "--inbox", str(inbox), "--cursor", str(cursor)])
    assert rc == 0
    assert not inbox.exists()


def test_bus_watch_codex_filters_by_to_agent(tmp_path):
    bus = tmp_path / "bus.jsonl"
    inbox = tmp_path / "inbox.md"
    cursor = tmp_path / "cursor.txt"
    _send(bus, to="openclaw", text="for openclaw, ignore me")
    _send(bus, to="codex", text="for codex, take me")
    cli.main(["--bus", str(bus), "bus-watch-codex", "--inbox", str(inbox), "--cursor", str(cursor)])
    content = inbox.read_text(encoding="utf-8")
    assert "for codex" in content
    assert "for openclaw" not in content


def test_bus_poll_returns_and_advances(tmp_path):
    bus = tmp_path / "bus.jsonl"
    cursor = tmp_path / "cursor.txt"
    _send(bus, to="openclaw", text="msg 1")
    _send(bus, to="openclaw", text="msg 2")
    rc = cli.main(["--bus", str(bus), "bus-poll", "--to", "openclaw", "--cursor", str(cursor)])
    assert rc == 0
    cursor_text = cursor.read_text(encoding="utf-8")
    assert cursor_text.startswith("msg-") and len(cursor_text) > 4


def test_bus_poll_no_messages(tmp_path):
    bus = tmp_path / "bus.jsonl"
    cursor = tmp_path / "cursor.txt"
    rc = cli.main(["--bus", str(bus), "bus-poll", "--to", "openclaw", "--cursor", str(cursor)])
    assert rc == 0
    assert not cursor.exists()

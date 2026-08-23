"""
Tests for the OpenClaw D2 sidecar (examples/openclaw_sidecar.py).

Tests building blocks without real LLM calls.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentos.bus.jsonl import JSONLBus
from agentos.schemas.message import Message, MessageType, Priority

sidecar_path = Path(__file__).resolve().parent.parent / "examples" / "openclaw_sidecar.py"
import importlib.util
_spec = importlib.util.spec_from_file_location("openclaw_sidecar", sidecar_path)
sidecar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sidecar)
_build_brief = sidecar._build_brief
_handle_message = sidecar._handle_message


def _make_msg(from_="codex", payload=None, msg_id="msg-test-1"):
    return Message(
        id=msg_id, from_agent=from_, to_agent="openclaw",
        type=MessageType.HANDOFF, priority=Priority.NORMAL,
        payload=payload or {},
    )


# _build_brief tests (5 paths per actual implementation)

def test_build_brief_text_payload():
    out = _build_brief(_make_msg(payload={"text": "hello world"}))
    assert out == "hello world"


def test_build_brief_codex_subject_message():
    out = _build_brief(_make_msg(payload={"subject": "the subject", "message": "the body"}))
    assert out == "the subject\n\nthe body"


def test_build_brief_codex_file_content():
    out = _build_brief(_make_msg(payload={"file": "/path/to/x.md", "content": "x body text"}))
    assert out == "x body text"


def test_build_brief_content_string():
    out = _build_brief(_make_msg(payload={"content": "raw content"}))
    assert out == "raw content"


def test_build_brief_content_dict_with_text():
    out = _build_brief(_make_msg(payload={"content": {"text": "from dict"}}))
    assert out == "from dict"


def test_build_brief_empty_payload_uses_placeholder():
    out = _build_brief(_make_msg(payload={}, msg_id="msg-empty-1"))
    assert "msg-empty-1" in out
    assert "codex" in out


# _handle_message tests

def _make_driver(reply="ok"):
    d = MagicMock()
    d.chat = AsyncMock(return_value=MagicMock(content=reply, usage={}, metadata={}))
    return d


def _make_args(dry_run=False):
    import argparse
    return argparse.Namespace(dry_run=dry_run)


@pytest.mark.asyncio
async def test_handle_calls_driver_with_brief():
    m = _make_msg(payload={"text": "hi openclaw"})
    d = _make_driver("sidecar says hi back")
    sem = asyncio.Semaphore(2)
    await _handle_message(m, d, sem, dry_run=False)
    d.chat.assert_awaited_once()
    brief = d.chat.await_args.kwargs.get("brief") or d.chat.await_args.args[0]
    assert "hi openclaw" in brief


@pytest.mark.asyncio
async def test_handle_dry_run_does_not_call_driver():
    m = _make_msg(payload={"text": "dry"})
    d = _make_driver()
    sem = asyncio.Semaphore(2)
    await _handle_message(m, d, sem, dry_run=True)
    d.chat.assert_not_called()


@pytest.mark.asyncio
async def test_handle_handles_driver_exception():
    m = _make_msg(payload={"text": "doomed"})
    d = MagicMock()
    d.chat = AsyncMock(side_effect=RuntimeError("OpenClaw down"))
    sem = asyncio.Semaphore(2)
    await _handle_message(m, d, sem, dry_run=False)  # should not raise


@pytest.mark.asyncio
async def test_handle_holds_semaphore_during_call():
    in_call = asyncio.Event()
    can_exit = asyncio.Event()
    async def slow_chat(*a, **k):
        in_call.set()
        await can_exit.wait()
        return MagicMock(content="ok", usage={}, metadata={})
    d = MagicMock()
    d.chat = AsyncMock(side_effect=slow_chat)
    sem = asyncio.Semaphore(1)
    m = _make_msg(payload={"text": "a"})
    task = asyncio.create_task(_handle_message(m, d, sem, dry_run=False))
    await in_call.wait()
    assert sem.locked()
    can_exit.set()
    await task
    assert not sem.locked()


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

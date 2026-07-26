"""Tests for telemetry hooks (JSONLHook + wrap_driver + wrap_handler)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agentos.drivers.base import ChatResult
from agentos.schemas.message import Message, MessageType, Priority
from agentos.telemetry import (
    DEFAULT_TELEMETRY_DIR,
    JSONLHook,
    TelemetryEvent,
    TelemetryEventType,
    default_hook,
    is_telemetry_enabled,
)
from agentos.telemetry.jsonl import default_hook as default_hook_module


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def telemetry_dir(tmp_path: Path, monkeypatch) -> Path:
    """Use a tmp dir for telemetry so we don't pollute G:/AgentOS/telemetry."""
    d = tmp_path / "telemetry"
    d.mkdir()
    monkeypatch.setenv("AGENTOS_TELEMETRY", "on")
    return d


@pytest.fixture(autouse=True)
def _reset_default_hook():
    """Reset the module-level singleton between tests."""
    default_hook_module.__dict__["_default_hook"] = None
    yield
    default_hook_module.__dict__["_default_hook"] = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


@dataclass
class FakeChatResult:
    """Mimics Codex ChatResult (fields: content, usage, metadata)."""

    content: str = "fake reply"
    usage: dict[str, int] | None = None
    metadata: dict[str, Any] | None = None


class FakeSyncDriver:
    """Mimics a sync driver.chat(brief, attachments, session_key, tool_subset)."""

    name = "FakeSyncDriver"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def chat(
        self,
        brief,
        *,
        attachments=None,
        session_key=None,
        tool_subset=None,
    ):
        if self.fail:
            raise RuntimeError("fake driver kaboom")
        return FakeChatResult(
            content=f"reply to: {brief}",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )


class FakeAsyncDriver:
    """Mimics the async Codex driver interface (await driver.chat(...))."""

    name = "FakeAsyncDriver"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def chat(
        self,
        brief,
        *,
        attachments=None,
        session_key=None,
        tool_subset=None,
    ):
        if self.fail:
            raise RuntimeError("fake async driver kaboom")
        return ChatResult(
            content=f"async reply to: {brief}",
            usage={"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        )


def _make_msg(**kw) -> Message:
    defaults = dict(
        id="msg-test",
        from_agent="codex",
        to_agent="openclaw",
        type=MessageType.HANDOFF,
        priority=Priority.NORMAL,
        payload={"text": "hello"},
    )
    defaults.update(kw)
    return Message(**defaults)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# is_telemetry_enabled
# --------------------------------------------------------------------------- #


def test_is_telemetry_enabled_default(monkeypatch):
    monkeypatch.delenv("AGENTOS_TELEMETRY", raising=False)
    assert is_telemetry_enabled() is True


@pytest.mark.parametrize("val", ["off", "OFF", "0", "false", "no"])
def test_is_telemetry_disabled(monkeypatch, val):
    monkeypatch.setenv("AGENTOS_TELEMETRY", val)
    assert is_telemetry_enabled() is False


# --------------------------------------------------------------------------- #
# JSONLHook.record
# --------------------------------------------------------------------------- #


def test_record_writes_to_dated_file(telemetry_dir: Path):
    hook = JSONLHook(telemetry_dir)
    hook.record(
        TelemetryEventType.BUS_MESSAGE_IN,
        from_agent="codex",
        to_agent="openclaw",
        payload={"id": "msg-1"},
    )
    today = datetime.now(timezone.utc).date().isoformat()
    path = telemetry_dir / f"{today}.jsonl"
    assert path.exists()
    events = _read_jsonl(path)
    assert len(events) == 1
    assert events[0]["event_type"] == "bus_message_in"
    assert events[0]["from_agent"] == "codex"
    assert events[0]["payload"]["id"] == "msg-1"


def test_record_disabled_does_not_write(telemetry_dir: Path, monkeypatch):
    monkeypatch.setenv("AGENTOS_TELEMETRY", "off")
    hook = JSONLHook(telemetry_dir, enabled=False)
    hook.record(TelemetryEventType.BUS_MESSAGE_IN, payload={"id": "x"})
    assert not any(telemetry_dir.glob("*.jsonl"))


def test_record_accepts_string_event_type(telemetry_dir: Path):
    hook = JSONLHook(telemetry_dir)
    hook.record("error", payload={"error": "boom"})
    today = datetime.now(timezone.utc).date().isoformat()
    events = _read_jsonl(telemetry_dir / f"{today}.jsonl")
    assert events[0]["event_type"] == "error"


def test_record_handles_write_failure(telemetry_dir: Path, monkeypatch):
    hook = JSONLHook(telemetry_dir)
    # Simulate I/O error by pointing the base_dir at a path that becomes unwritable.
    monkeypatch.setattr(hook, "base_dir", Path("Z:/nope/cannot/write"))
    # Should NOT raise — errors are swallowed + logged.
    hook.record(TelemetryEventType.BUS_MESSAGE_IN, payload={"id": "x"})


# --------------------------------------------------------------------------- #
# JSONLHook.wrap_driver — sync
# --------------------------------------------------------------------------- #


def test_wrap_driver_sync_records_in_and_out(telemetry_dir: Path):
    hook = JSONLHook(telemetry_dir)
    wrapped = hook.wrap_driver(FakeSyncDriver())
    result = wrapped.chat("hello world", session_key="task:t1:stage:s1")
    assert result.content == "reply to: hello world"

    today = datetime.now(timezone.utc).date().isoformat()
    events = _read_jsonl(telemetry_dir / f"{today}.jsonl")
    types = [e["event_type"] for e in events]
    assert types == ["driver_chat_in", "driver_chat_out"]
    assert all(e["driver"] == "FakeSyncDriver" for e in events)
    assert all(e["session_key"] == "task:t1:stage:s1" for e in events)
    out_event = events[1]
    assert "latency_ms" in out_event["metadata"]
    assert out_event["metadata"]["token_usage"] == {
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
    }


def test_wrap_driver_sync_records_error(telemetry_dir: Path):
    hook = JSONLHook(telemetry_dir)
    wrapped = hook.wrap_driver(FakeSyncDriver(fail=True))
    with pytest.raises(RuntimeError):
        wrapped.chat("hello", session_key="task:t1:stage:s1")

    today = datetime.now(timezone.utc).date().isoformat()
    events = _read_jsonl(telemetry_dir / f"{today}.jsonl")
    types = [e["event_type"] for e in events]
    assert types == ["driver_chat_in", "error"]


def test_wrap_driver_sync_preserves_other_attrs(telemetry_dir: Path):
    driver = FakeSyncDriver()
    hook = JSONLHook(telemetry_dir)
    wrapped = hook.wrap_driver(driver)
    # Attributes not on the wrapper should delegate to wrapped driver.
    assert wrapped.name == "FakeSyncDriver"
    assert wrapped.fail is False


# --------------------------------------------------------------------------- #
# JSONLHook.wrap_driver — async (Codex v0.1 vendor wrappers interface)
# --------------------------------------------------------------------------- #


def test_wrap_driver_async_records_in_and_out(telemetry_dir: Path):
    hook = JSONLHook(telemetry_dir)
    wrapped = hook.wrap_driver(FakeAsyncDriver())

    async def go():
        result = await wrapped.chat("async hello", session_key="task:t2:stage:s2")
        return result

    result = asyncio.run(go())
    assert result.content == "async reply to: async hello"

    today = datetime.now(timezone.utc).date().isoformat()
    events = _read_jsonl(telemetry_dir / f"{today}.jsonl")
    types = [e["event_type"] for e in events]
    assert types == ["driver_chat_in", "driver_chat_out"]
    assert all(e["driver"] == "FakeAsyncDriver" for e in events)
    out_event = events[1]
    assert out_event["metadata"]["token_usage"]["total_tokens"] == 28


def test_wrap_driver_async_records_error(telemetry_dir: Path):
    hook = JSONLHook(telemetry_dir)
    wrapped = hook.wrap_driver(FakeAsyncDriver(fail=True))

    async def go():
        return await wrapped.chat("boom", session_key="task:t2:stage:s2")

    with pytest.raises(RuntimeError):
        asyncio.run(go())

    today = datetime.now(timezone.utc).date().isoformat()
    events = _read_jsonl(telemetry_dir / f"{today}.jsonl")
    types = [e["event_type"] for e in events]
    assert types == ["driver_chat_in", "error"]


# --------------------------------------------------------------------------- #
# JSONLHook.wrap_handler
# --------------------------------------------------------------------------- #


def test_wrap_handler_records_each_call(telemetry_dir: Path):
    hook = JSONLHook(telemetry_dir)
    received: list[Message] = []

    def handler(msg: Message) -> None:
        received.append(msg)

    wrapped_handler = hook.wrap_handler(handler, TelemetryEventType.BUS_MESSAGE_IN)
    msg = _make_msg()
    wrapped_handler(msg)
    wrapped_handler(msg)
    assert len(received) == 2

    today = datetime.now(timezone.utc).date().isoformat()
    events = _read_jsonl(telemetry_dir / f"{today}.jsonl")
    assert len(events) == 2
    assert all(e["event_type"] == "bus_message_in" for e in events)
    assert all(e["from_agent"] == "codex" for e in events)


# --------------------------------------------------------------------------- #
# default_hook singleton
# --------------------------------------------------------------------------- #


def test_default_hook_is_singleton():
    h1 = default_hook()
    h2 = default_hook()
    assert h1 is h2


def test_default_hook_path_default():
    h = JSONLHook()
    assert h.base_dir == Path("G:/AgentOS/telemetry")
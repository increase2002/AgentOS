"""Tests for the multi-LLM dispatcher table in examples/openclaw_sidecar.py.

Covers:
- _build_dispatcher_table returns the expected keys
- _dispatch_openclaw calls the OpenClaw driver with (brief, session_key) and
  writes the reply back via _bus_send
- _dispatch_codex calls the Codex adapter with (brief, session_key) and writes
  the reply back via _bus_send (with from_agent='codex')
- _dispatch_unknown logs + skips without touching any LLM
- _handle_message routes by msg.to_agent
- A dispatcher crash is contained (the handler survives)
- Semaphore is honoured (concurrent dispatcher calls gated)

No real LLM calls; uses FakeDriver + FakeCodexAdapter. Bus writes are
captured via monkeypatching ``_bus_send``.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import examples.openclaw_sidecar as sidecar  # noqa: E402
from agentos.schemas.message import (  # noqa: E402
    Message,
    MessageType,
    Priority,
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


@dataclass
class FakeChatResult:
    content: str = "fake-reply"


@dataclass
class FakeDriver:
    """Mimics the bits of OpenClawDriver the dispatcher touches."""

    name: str = "fake-openclaw"
    calls: list[dict[str, Any]] = field(default_factory=list)
    fail_with: Exception | None = None

    async def chat(self, brief: str, *, session_key: str | None = None) -> FakeChatResult:
        self.calls.append({"brief": brief, "session_key": session_key})
        if self.fail_with is not None:
            raise self.fail_with
        return FakeChatResult(content=f"openclaw-reply:{brief[:30]}")


@dataclass
class FakeCodexAdapter:
    """Mimics the bits of CodexAdapter the dispatcher touches."""

    name: str = "fake-codex"
    calls: list[dict[str, Any]] = field(default_factory=list)
    fail_with: Exception | None = None

    async def chat(
        self,
        brief: str,
        *,
        session_key: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        tool_subset: list[str] | None = None,
    ) -> FakeChatResult:
        self.calls.append({
            "brief": brief,
            "session_key": session_key,
            "attachments": attachments,
            "tool_subset": tool_subset,
        })
        if self.fail_with is not None:
            raise self.fail_with
        return FakeChatResult(content=f"codex-reply:{brief[:30]}")


def _msg(payload: dict, *, to_agent: str = "openclaw") -> Message:
    return Message(
        id="m-1",
        from_agent="codex",
        to_agent=to_agent,
        type=MessageType.HANDOFF,
        priority=Priority.NORMAL,
        payload=payload,
    )


# --------------------------------------------------------------------------- #
# _build_dispatcher_table
# --------------------------------------------------------------------------- #


def test_dispatcher_table_has_openclaw_and_codex() -> None:
    table = sidecar._build_dispatcher_table()
    assert set(table.keys()) == {"openclaw", "codex"}
    assert table["openclaw"] is sidecar._dispatch_openclaw
    assert table["codex"] is sidecar._dispatch_codex


# --------------------------------------------------------------------------- #
# _dispatch_openclaw
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_dispatch_openclaw_calls_driver_and_bus_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = FakeDriver()
    ctx = sidecar.DispatchContext(openclaw_driver=driver)
    msg = _msg({"text": "hello"}, to_agent="openclaw")
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        sidecar, "_bus_send",
        lambda **kw: captured.append(kw),
    )

    await sidecar._dispatch_openclaw(msg, ctx)

    assert len(driver.calls) == 1
    assert driver.calls[0]["brief"] == "hello"
    assert driver.calls[0]["session_key"] == "task:m-1:stage:sidecar-openclaw"
    assert len(captured) == 1
    assert captured[0]["from_agent"] == "openclaw"
    assert captured[0]["to_agent"] == "codex"
    assert captured[0]["task"] == "m-1"
    assert "openclaw-reply:hello" in captured[0]["text"]


@pytest.mark.asyncio
async def test_dispatch_openclaw_dry_run_skips_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = FakeDriver()
    ctx = sidecar.DispatchContext(openclaw_driver=driver, dry_run=True)
    msg = _msg({"text": "hi"}, to_agent="openclaw")
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(sidecar, "_bus_send", lambda **kw: captured.append(kw))

    await sidecar._dispatch_openclaw(msg, ctx)

    assert driver.calls == []
    assert captured == []  # dry-run: no reply written


@pytest.mark.asyncio
async def test_dispatch_openclaw_driver_error_writes_error_bus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = FakeDriver(fail_with=RuntimeError("boom"))
    ctx = sidecar.DispatchContext(openclaw_driver=driver)
    msg = _msg({"text": "x"}, to_agent="openclaw")
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(sidecar, "_bus_send", lambda **kw: captured.append(kw))

    await sidecar._dispatch_openclaw(msg, ctx)

    assert len(captured) == 1
    assert captured[0]["priority"] == "HIGH"
    assert "ERROR" in captured[0]["text"]
    assert "RuntimeError" in captured[0]["text"]
    assert "boom" in captured[0]["text"]


# --------------------------------------------------------------------------- #
# _dispatch_codex
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_dispatch_codex_calls_codex_adapter_and_bus_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex = FakeCodexAdapter()
    ctx = sidecar.DispatchContext(codex_adapter=codex)
    msg = _msg({"text": "codex-please"}, to_agent="codex")
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(sidecar, "_bus_send", lambda **kw: captured.append(kw))

    await sidecar._dispatch_codex(msg, ctx)

    assert len(codex.calls) == 1
    assert codex.calls[0]["brief"] == "codex-please"
    assert codex.calls[0]["session_key"] == "task:m-1:stage:sidecar-codex"
    assert len(captured) == 1
    # Reply is from_agent=codex (the dispatcher runs on Codex's behalf)
    assert captured[0]["from_agent"] == "codex"
    assert captured[0]["to_agent"] == "codex"  # reply back to original sender (codex)
    assert captured[0]["task"] == "m-1"
    assert "codex-reply:codex-please" in captured[0]["text"]


@pytest.mark.asyncio
async def test_dispatch_codex_dry_run_skips_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex = FakeCodexAdapter()
    ctx = sidecar.DispatchContext(codex_adapter=codex, dry_run=True)
    msg = _msg({"text": "ping"}, to_agent="codex")
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(sidecar, "_bus_send", lambda **kw: captured.append(kw))

    await sidecar._dispatch_codex(msg, ctx)

    assert codex.calls == []
    assert captured == []


@pytest.mark.asyncio
async def test_dispatch_codex_adapter_error_writes_error_bus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex = FakeCodexAdapter(fail_with=RuntimeError("codex-cli-exit-1"))
    ctx = sidecar.DispatchContext(codex_adapter=codex)
    msg = _msg({"text": "x"}, to_agent="codex")
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(sidecar, "_bus_send", lambda **kw: captured.append(kw))

    await sidecar._dispatch_codex(msg, ctx)

    assert len(captured) == 1
    assert captured[0]["priority"] == "HIGH"
    assert captured[0]["from_agent"] == "codex"
    assert "ERROR" in captured[0]["text"]
    assert "codex-cli-exit-1" in captured[0]["text"]


# --------------------------------------------------------------------------- #
# _dispatch_unknown
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_dispatch_unknown_logs_and_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = sidecar.DispatchContext(
        openclaw_driver=FakeDriver(),  # present but should NOT be touched
    )
    msg = _msg({"text": "x"}, to_agent="anthropic")
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(sidecar, "_bus_send", lambda **kw: captured.append(kw))

    await sidecar._dispatch_unknown(msg, ctx)

    assert ctx.openclaw_driver.calls == []
    assert captured == []


# --------------------------------------------------------------------------- #
# _handle_message (routing + containment + semaphore)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_handle_message_routes_to_openclaw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = FakeDriver()
    codex = FakeCodexAdapter()
    ctx = sidecar.DispatchContext(openclaw_driver=driver, codex_adapter=codex)
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(sidecar, "_bus_send", lambda **kw: captured.append(kw))
    msg = _msg({"text": "hello"}, to_agent="openclaw")

    await sidecar._handle_message(
        msg, sidecar._build_dispatcher_table(), ctx, asyncio.Semaphore(2), None,
    )

    assert len(driver.calls) == 1
    assert codex.calls == []


@pytest.mark.asyncio
async def test_handle_message_routes_to_codex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = FakeDriver()
    codex = FakeCodexAdapter()
    ctx = sidecar.DispatchContext(openclaw_driver=driver, codex_adapter=codex)
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(sidecar, "_bus_send", lambda **kw: captured.append(kw))
    msg = _msg({"text": "ping"}, to_agent="codex")

    await sidecar._handle_message(
        msg, sidecar._build_dispatcher_table(), ctx, asyncio.Semaphore(2), None,
    )

    assert len(codex.calls) == 1
    assert driver.calls == []


@pytest.mark.asyncio
async def test_handle_message_unknown_routes_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = FakeDriver()
    codex = FakeCodexAdapter()
    ctx = sidecar.DispatchContext(openclaw_driver=driver, codex_adapter=codex)
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(sidecar, "_bus_send", lambda **kw: captured.append(kw))
    msg = _msg({"text": "x"}, to_agent="anthropic")

    # Must not raise; unknown dispatch is a controlled skip
    await sidecar._handle_message(
        msg, sidecar._build_dispatcher_table(), ctx, asyncio.Semaphore(2), None,
    )

    assert driver.calls == []
    assert codex.calls == []


@pytest.mark.asyncio
async def test_handle_message_dispatcher_crash_is_contained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A buggy dispatcher must not propagate to the handler caller."""

    async def boom(msg: Message, ctx: sidecar.DispatchContext) -> None:
        raise RuntimeError("intentional crash")

    dispatchers = {"openclaw": boom}
    ctx = sidecar.DispatchContext(openclaw_driver=FakeDriver())
    msg = _msg({"text": "x"}, to_agent="openclaw")

    # Must not raise
    await sidecar._handle_message(
        msg, dispatchers, ctx, asyncio.Semaphore(2), None,
    )


@pytest.mark.asyncio
async def test_handle_message_semaphore_gates_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With concurrency=1, two concurrent dispatches serialize."""
    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def slow_dispatch(msg: Message, ctx: sidecar.DispatchContext) -> None:
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1

    dispatchers = {"openclaw": slow_dispatch}
    ctx = sidecar.DispatchContext()
    sem = asyncio.Semaphore(1)

    # Launch 3 concurrent handler calls; semaphore=1 must serialize them.
    await asyncio.gather(*[
        sidecar._handle_message(
            _msg({"text": f"t{i}"}, to_agent="openclaw"),
            dispatchers, ctx, sem, None,
        )
        for i in range(3)
    ])

    assert max_in_flight == 1, (
        f"semaphore did not serialize; max concurrent = {max_in_flight}"
    )


# --------------------------------------------------------------------------- #
# DispatchContext (smoke)
# --------------------------------------------------------------------------- #


def test_dispatch_context_default_values() -> None:
    ctx = sidecar.DispatchContext()
    assert ctx.openclaw_driver is None
    assert ctx.codex_adapter is None
    assert ctx.dry_run is False


def test_dispatch_context_with_all_fields() -> None:
    ctx = sidecar.DispatchContext(
        openclaw_driver=FakeDriver(),
        codex_adapter=FakeCodexAdapter(),
        dry_run=True,
    )
    assert isinstance(ctx.openclaw_driver, FakeDriver)
    assert isinstance(ctx.codex_adapter, FakeCodexAdapter)
    assert ctx.dry_run is True

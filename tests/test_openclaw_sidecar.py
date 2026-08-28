"""Tests for the OpenClaw D2 sidecar (examples/openclaw_sidecar.py).

Focus on the pure helpers (no real LLM, no bus file). Covers:
- _build_brief: payload shape extraction
- _bus_send: subprocess invocation shape (mocked)
- _handle_message rate-limit gate: token bucket exhaustion short-circuits
  before the LLM call and emits a [RATE_LIMITED] HANDOFF instead

Uses the dispatcher table introduced by Codex on 2026-08-28 (commit
a31c4e9) so the rate-limit gate is exercised against the real dispatch
path (openclaw + codex + unknown).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import examples.openclaw_sidecar as sidecar  # noqa: E402
from agentos.core.token_bucket import TokenBucket  # noqa: E402
from agentos.schemas.message import (  # noqa: E402
    Message,
    MessageType,
    Priority,
)


def _msg(payload: dict, *, to_agent: str = "openclaw") -> Message:
    return Message(
        id="m-1",
        from_agent="codex",
        to_agent=to_agent,
        type=MessageType.HANDOFF,
        priority=Priority.NORMAL,
        payload=payload,
    )


# ----- _build_brief ---------------------------------------------------- #


def test_build_brief_text_field() -> None:
    m = _msg({"text": "hello"})
    assert sidecar._build_brief(m) == "hello"


def test_build_brief_subject_message() -> None:
    m = _msg({"subject": "Ping", "message": "are you there?"})
    out = sidecar._build_brief(m)
    assert "Ping" in out
    assert "are you there?" in out


def test_build_brief_file_content() -> None:
    m = _msg({"file": "report.md", "content": "# body"})
    assert sidecar._build_brief(m) == "# body"


def test_build_brief_content_string() -> None:
    m = _msg({"content": "raw content"})
    assert sidecar._build_brief(m) == "raw content"


def test_build_brief_content_dict() -> None:
    m = _msg({"content": {"text": "nested"}})
    assert sidecar._build_brief(m) == "nested"


def test_build_brief_empty_payload_falls_back() -> None:
    m = _msg({"task_id": "t-x", "unrelated": 1})
    out = sidecar._build_brief(m)
    assert "m-1" in out
    assert "task_id" in out or "t-x" in out  # any usable identifier


def test_build_brief_empty_payload_actually_empty() -> None:
    m = _msg({})
    out = sidecar._build_brief(m)
    assert "m-1" in out


# ----- fakes ------------------------------------------------------------ #


@dataclass
class FakeChatResult:
    content: str = "fake-reply"


@dataclass
class FakeDriver:
    """Mimics OpenClawDriver.chat."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def chat(self, brief: str, *, session_key: str | None = None) -> FakeChatResult:
        self.calls.append({"brief": brief, "session_key": session_key})
        return FakeChatResult(content=f"openclaw-reply:{brief[:30]}")


@dataclass
class FakeCodexAdapter:
    """Mimics CodexAdapter.chat."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def chat(
        self, brief: str, *, session_key: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        tool_subset: list[str] | None = None,
    ) -> FakeChatResult:
        self.calls.append({"brief": brief, "session_key": session_key})
        return FakeChatResult(content=f"codex-reply:{brief[:30]}")


def _capture_bus_send(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    def fake_send(
        *, to_agent, from_agent, text, task,
        msg_type="HANDOFF", priority="NORMAL",
    ):
        captured.append({
            "to_agent": to_agent, "from_agent": from_agent, "text": text,
            "task": task, "type": msg_type, "priority": priority,
        })
    monkeypatch.setattr(sidecar, "_bus_send", fake_send)
    return captured


# ----- rate limit gate in _handle_message ------------------------------- #


def _fake_args() -> argparse.Namespace:
    return argparse.Namespace(
        rate_rpm=60, burst=2, no_rate_limit=False,
    )


def test_handle_message_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """When bucket is empty, handler sends a [RATE_LIMITED] HANDOFF
    and does NOT touch any dispatcher / LLM."""
    captured = _capture_bus_send(monkeypatch)
    driver = FakeDriver()
    codex = FakeCodexAdapter()
    ctx = sidecar.DispatchContext(openclaw_driver=driver, codex_adapter=codex)

    bucket = TokenBucket(capacity=2, refill_rate=0.0001)
    assert bucket.try_consume().allowed
    assert bucket.try_consume().allowed
    assert not bucket.try_consume().allowed  # bucket empty

    msg = _msg({"text": "hi", "task_id": "t-42"}, to_agent="openclaw")

    asyncio.run(
        sidecar._handle_message(
            msg, sidecar._build_dispatcher_table(), ctx,
            asyncio.Semaphore(1), bucket,
        )
    )

    assert len(captured) == 1
    s = captured[0]
    assert s["from_agent"] == "openclaw"
    assert s["to_agent"] == "codex"  # replied to original sender
    assert s["type"] == "HANDOFF"
    assert s["priority"] == "HIGH"
    assert s["task"] == "t-42"
    assert "[RATE_LIMITED]" in s["text"]
    assert "msg m-1" in s["text"]
    assert "to=openclaw" in s["text"]

    # No LLM was called
    assert driver.calls == []
    assert codex.calls == []


def test_handle_message_passes_when_bucket_has_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """When bucket has tokens, the dispatcher table routes normally."""
    captured = _capture_bus_send(monkeypatch)
    driver = FakeDriver()
    codex = FakeCodexAdapter()
    ctx = sidecar.DispatchContext(openclaw_driver=driver, codex_adapter=codex)

    bucket = TokenBucket(capacity=5, refill_rate=1.0)
    msg = _msg({"text": "hello", "task_id": "t-7"}, to_agent="openclaw")

    asyncio.run(
        sidecar._handle_message(
            msg, sidecar._build_dispatcher_table(), ctx,
            asyncio.Semaphore(1), bucket,
        )
    )

    assert len(captured) == 1
    assert "[RATE_LIMITED]" not in captured[0]["text"]
    assert "openclaw-reply:hello" in captured[0]["text"]
    assert len(driver.calls) == 1
    assert codex.calls == []


def test_handle_message_rate_limit_blocks_codex_dispatch_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rate limit gates ALL dispatchers (openclaw + codex), not just one."""
    captured = _capture_bus_send(monkeypatch)
    driver = FakeDriver()
    codex = FakeCodexAdapter()
    ctx = sidecar.DispatchContext(openclaw_driver=driver, codex_adapter=codex)

    bucket = TokenBucket(capacity=1, refill_rate=0.0001)
    assert bucket.try_consume().allowed
    assert not bucket.try_consume().allowed  # empty

    msg = _msg({"text": "codex please", "task_id": "t-9"}, to_agent="codex")

    asyncio.run(
        sidecar._handle_message(
            msg, sidecar._build_dispatcher_table(), ctx,
            asyncio.Semaphore(1), bucket,
        )
    )

    assert len(captured) == 1
    assert "[RATE_LIMITED]" in captured[0]["text"]
    assert "to=codex" in captured[0]["text"]
    assert driver.calls == []
    assert codex.calls == []  # codex adapter NOT called


def test_handle_message_no_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """When rate_limiter is None, dispatch table routes normally."""
    captured = _capture_bus_send(monkeypatch)
    driver = FakeDriver()
    ctx = sidecar.DispatchContext(openclaw_driver=driver)

    # Drive 20 messages without limiter — no rejections
    for i in range(20):
        m = _msg({"text": f"msg-{i}", "task_id": "t-1"}, to_agent="openclaw")
        asyncio.run(
            sidecar._handle_message(
                m, sidecar._build_dispatcher_table(), ctx,
                asyncio.Semaphore(1), None,
            )
        )

    assert len(captured) == 20
    assert all("[RATE_LIMITED]" not in s["text"] for s in captured)
    assert len(driver.calls) == 20


def test_handle_message_dry_run_skips_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In dry-run mode the rate limiter must not gate messages."""
    captured = _capture_bus_send(monkeypatch)
    ctx = sidecar.DispatchContext(dry_run=True)

    bucket = TokenBucket(capacity=1, refill_rate=0.0001)
    assert bucket.try_consume().allowed
    assert not bucket.try_consume().allowed  # bucket empty

    msg = _msg({"text": "dry"}, to_agent="openclaw")
    asyncio.run(
        sidecar._handle_message(
            msg, sidecar._build_dispatcher_table(), ctx,
            asyncio.Semaphore(1), bucket,
        )
    )

    # dry-run: no reply sent, but also no [RATE_LIMITED] sent
    assert captured == []


# ----- _build_rate_limiter --------------------------------------------- #


def test_build_rate_limiter_no_rate_limit_returns_none() -> None:
    args = _fake_args()
    args.no_rate_limit = True
    assert sidecar._build_rate_limiter(args, have_llm=True) is None


def test_build_rate_limiter_dry_run_returns_none() -> None:
    args = _fake_args()
    assert sidecar._build_rate_limiter(args, have_llm=False) is None


def test_build_rate_limiter_constructs_bucket() -> None:
    args = _fake_args()  # rate_rpm=60, burst=2
    b = sidecar._build_rate_limiter(args, have_llm=True)
    assert isinstance(b, TokenBucket)
    assert b.capacity == 2
    assert b.refill_rate == 1.0  # 60 rpm = 1 tok/s
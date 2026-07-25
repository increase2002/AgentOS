"""Smoke tests for the Driver interface."""

from __future__ import annotations

import pytest

from agentos.drivers import OpenAIDriver, WSDriver
from agentos.drivers.base import BaseDriver, ChatResult, DriverError


def test_openaidriver_requires_config() -> None:
    with pytest.raises(DriverError, match="base_url"):
        OpenAIDriver("bad", {})


def test_wsdiver_requires_token() -> None:
    with pytest.raises(DriverError, match="token"):
        WSDriver("openclaw-main", {"ws_url": "ws://127.0.0.1:18789"})


def test_chat_result_defaults() -> None:
    r = ChatResult(content="hello")
    assert r.content == "hello"
    assert r.artifact is None
    assert r.usage is None
    assert r.metadata == {}


def test_basedriver_is_abstract() -> None:
    # BaseDriver itself is abstract and cannot be instantiated directly.
    with pytest.raises(TypeError):
        BaseDriver("x", {})  # type: ignore[abstract]

# ---------------------------------------------------------------------------
# OpenAIDriver._build_messages — tool_subset enforcement (MVP, soft prompt)
# ---------------------------------------------------------------------------


def _driver() -> OpenAIDriver:
    """Construct an OpenAIDriver without hitting the network."""
    return OpenAIDriver(
        "test",
        {"base_url": "https://example.invalid", "api_key": "sk-test"},
    )


def test_build_messages_no_constraint() -> None:
    msgs = _driver()._build_messages("hello")
    assert len(msgs) == 1
    assert msgs[0] == {"role": "user", "content": "hello"}


def test_build_messages_with_tool_subset() -> None:
    msgs = _driver()._build_messages(
        "analyze this",
        tool_subset=["read_file", "grep"],
    )
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    body = msgs[0]["content"]
    assert "read_file" in body
    assert "grep" in body
    assert "refuse" in body.lower()
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "analyze this"


def test_build_messages_plan_only_empty_subset() -> None:
    msgs = _driver()._build_messages("draft a plan", tool_subset=[])
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    body = msgs[0]["content"].lower()
    assert "plan-only" in body or "read-only" in body
    assert "do not" in body or "must not" in body
    assert msgs[1]["content"] == "draft a plan"


def test_build_messages_with_attachments_and_tools() -> None:
    msgs = _driver()._build_messages(
        "process this",
        attachments=[{"name": "report.md", "content": "# Title\nbody"}],
        tool_subset=["read_file"],
    )
    assert len(msgs) == 3
    assert msgs[0]["role"] == "system"
    assert "read_file" in msgs[0]["content"]
    assert msgs[1] == {"role": "user", "content": "process this"}
    assert "report.md" in msgs[2]["content"]
    assert "# Title" in msgs[2]["content"]

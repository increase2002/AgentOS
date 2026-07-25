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
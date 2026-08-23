"""Tests for GeminiDriver."""

from __future__ import annotations

import pytest

from agentos.drivers import GeminiDriver
from agentos.drivers.base import DriverError


def test_gemini_driver_defaults() -> None:
    g = GeminiDriver("test", {"api_key": "test-key"})
    assert "generativelanguage.googleapis.com" in str(g.client.base_url)
    assert "openai" in str(g.client.base_url)
    assert g.default_model == "gemini-2.0-flash"


def test_gemini_driver_custom_base_url() -> None:
    g = GeminiDriver("test", {
        "base_url": "https://custom.googleapis.example/v1/openai/",
        "api_key": "test-key",
    })
    assert "custom.googleapis.example" in str(g.client.base_url)


def test_gemini_driver_custom_model() -> None:
    g = GeminiDriver("test", {
        "api_key": "test-key",
        "default_model": "gemini-2.5-pro",
    })
    assert g.default_model == "gemini-2.5-pro"


def test_gemini_driver_requires_api_key() -> None:
    with pytest.raises(DriverError, match="api_key"):
        GeminiDriver("test", {})


def test_gemini_driver_is_openai_subclass() -> None:
    """GeminiDriver should inherit all OpenAIDriver capabilities."""
    from agentos.drivers.openai_driver import OpenAIDriver
    g = GeminiDriver("test", {"api_key": "test"})
    assert isinstance(g, OpenAIDriver)


def test_gemini_driver_extra_headers_preserved() -> None:
    g = GeminiDriver("test", {
        "api_key": "test",
        "extra_headers": {"X-Custom": "value"},
    })
    assert g.extra_headers == {"X-Custom": "value"}


def test_gemini_driver_inherits_tool_subset() -> None:
    """tool_subset enforcement is inherited from OpenAIDriver (ADR-0009)."""
    g = GeminiDriver("test", {"api_key": "test"})
    msgs = g._build_messages("hi", tool_subset=[])
    assert msgs[0]["role"] == "system"
    assert "plan-only" in msgs[0]["content"].lower()


def test_gemini_driver_auto_wraps_telemetry(monkeypatch) -> None:
    """ADR-0004: GeminiDriver auto-wires telemetry on construction."""
    monkeypatch.setenv("AGENTOS_TELEMETRY", "on")
    g = GeminiDriver("g", {"api_key": "k"})
    assert getattr(g, "_agentos_telemetry_wrapped", False) is True
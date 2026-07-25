"""Tests for AnthropicDriver.

HTTP is mocked via _post_json method override.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentos.drivers import AnthropicDriver
from agentos.drivers.base import DriverError


def test_anthropic_driver_requires_api_key() -> None:
    with pytest.raises(DriverError, match="api_key"):
        AnthropicDriver("test", {})


def test_anthropic_driver_defaults() -> None:
    a = AnthropicDriver("test", {"api_key": "sk-test"})
    assert a.base_url == "https://api.anthropic.com"
    assert a.default_model == "claude-sonnet-4-5"


def test_anthropic_driver_overrides() -> None:
    a = AnthropicDriver("test", {
        "api_key": "sk-test",
        "base_url": "https://custom.example.com",
        "default_model": "claude-opus-4-1",
        "max_tokens": 8192,
    })
    assert a.base_url == "https://custom.example.com"
    assert a.default_model == "claude-opus-4-1"
    assert a.max_tokens == 8192


@pytest.mark.asyncio
async def test_anthropic_driver_chat_parses_response() -> None:
    a = AnthropicDriver("test", {"api_key": "sk-test"})

    async def fake_post(path: str, body: dict[str, Any]):
        assert path == "/v1/messages"
        assert body["model"] == "claude-sonnet-4-5"
        assert body["messages"] == [{"role": "user", "content": "hi"}]
        return ({
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello back"}],
            "model": "claude-sonnet-4-5",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }, 200)

    a._post_json = fake_post
    result = await a.chat("hi")
    assert result.content == "Hello back"
    assert result.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    assert result.metadata["stop_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_anthropic_driver_concatenates_content_blocks() -> None:
    a = AnthropicDriver("test", {"api_key": "sk-test"})

    async def fake_post(path: str, body: dict[str, Any]):
        return ({
            "content": [
                {"type": "text", "text": "first "},
                {"type": "text", "text": "second"},
            ],
            "usage": {"input_tokens": 1, "output_tokens": 2},
        }, 200)

    a._post_json = fake_post
    result = await a.chat("hi")
    assert result.content == "first second"


@pytest.mark.asyncio
async def test_anthropic_driver_error_response_raises() -> None:
    a = AnthropicDriver("test", {"api_key": "sk-test"})

    async def fake_post(path: str, body: dict[str, Any]):
        return ({"type": "error", "error": {"message": "bad key"}}, 401)

    a._post_json = fake_post
    with pytest.raises(DriverError, match="401"):
        await a.chat("hi")


@pytest.mark.asyncio
async def test_anthropic_driver_plan_only_tool_subset() -> None:
    a = AnthropicDriver("test", {"api_key": "sk-test"})
    captured: dict[str, Any] = {}

    async def fake_post(path: str, body: dict[str, Any]):
        captured.update(body)
        return (
            {"content": [{"type": "text", "text": "plan"}], "usage": {}},
            200,
        )

    a._post_json = fake_post
    await a.chat("draft a plan", tool_subset=[])
    assert "plan-only" in captured["system"].lower()


@pytest.mark.asyncio
async def test_anthropic_driver_whitelist_tool_subset() -> None:
    a = AnthropicDriver("test", {"api_key": "sk-test"})
    captured: dict[str, Any] = {}

    async def fake_post(path: str, body: dict[str, Any]):
        captured.update(body)
        return (
            {"content": [{"type": "text", "text": "ok"}], "usage": {}},
            200,
        )

    a._post_json = fake_post
    await a.chat("analyze", tool_subset=["read_file", "grep"])
    assert "read_file" in captured["system"]
    assert "grep" in captured["system"]


@pytest.mark.asyncio
async def test_anthropic_driver_no_system_when_no_tool_subset() -> None:
    a = AnthropicDriver("test", {"api_key": "sk-test"})
    captured: dict[str, Any] = {}

    async def fake_post(path: str, body: dict[str, Any]):
        captured.update(body)
        return (
            {"content": [{"type": "text", "text": "hi"}], "usage": {}},
            200,
        )

    a._post_json = fake_post
    await a.chat("hi")  # no tool_subset
    assert "system" not in captured


@pytest.mark.asyncio
async def test_anthropic_driver_health_check() -> None:
    assert await AnthropicDriver("test", {"api_key": "sk-test"}).health_check() is True
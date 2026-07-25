"""Tests for OpenClaw driver + memory + config modules.

Coverage:
- openclaw_driver: defaults, session_key validation, missing token
- openclaw_memory: MVP stub returns structured empty result with metadata
- openclaw_config: JSON5 comment stripping, SecretRef handling, Contract B
  readiness check, full file load against a synthetic openclaw.json
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.drivers.openclaw_config import (
    SECRET_REF_PLACEHOLDER,
    OpenClawConfig,
    _strip_json5_comments,
    load_openclaw_config,
    parse_openclaw_config,
)
from agentos.drivers.openclaw_driver import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    OpenClawDriver,
)
from agentos.drivers.openclaw_memory import (
    OpenClawMemoryDriver,
)


# ---------------------------------------------------------------------------
# OpenClawDriver
# ---------------------------------------------------------------------------


def test_openclaw_driver_defaults() -> None:
    driver = OpenClawDriver("oc-main", {"api_key": "tok-123"})
    assert driver.name == "oc-main"
    assert driver.config["base_url"] == DEFAULT_BASE_URL
    assert driver.config["default_model"] == DEFAULT_MODEL
    assert driver.tool_registry is None
    assert driver.session_key_strategy == "explicit"


def test_openclaw_driver_requires_api_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        OpenClawDriver("oc-main", {})


def test_openclaw_driver_validate_session_key_rejects_reserved() -> None:
    for bad in ("subagent:foo", "cron:bar", "acp:baz"):
        with pytest.raises(ValueError, match="reserved prefix"):
            OpenClawDriver.validate_session_key(bad)


def test_openclaw_driver_validate_session_key_accepts_agentos_key() -> None:
    # AgentOS-format keys must NOT be rejected (per ADR-0006)
    OpenClawDriver.validate_session_key("task:abc:stage:research")
    OpenClawDriver.validate_session_key("task:abc:stage:code:sub:worker-1")
    # None is allowed (driver will use stateless mode)
    OpenClawDriver.validate_session_key(None)


def test_openclaw_driver_accepts_tool_registry_config() -> None:
    registry = {"read_file": {"type": "function"}, "web_search": {"type": "function"}}
    driver = OpenClawDriver(
        "oc-main",
        {"api_key": "tok", "tool_registry": registry},
    )
    assert driver.tool_registry == registry


# ---------------------------------------------------------------------------
# OpenClawMemoryDriver
# ---------------------------------------------------------------------------


def test_openclaw_memory_driver_defaults() -> None:
    drv = OpenClawMemoryDriver({"token": "tok"})
    assert drv.gateway_url == "ws://127.0.0.1:18789"
    assert drv.search_agent == OpenClawMemoryDriver.DEFAULT_SEARCH_AGENT


def test_openclaw_memory_driver_requires_token() -> None:
    with pytest.raises(ValueError, match="token"):
        OpenClawMemoryDriver({})


@pytest.mark.asyncio
async def test_openclaw_memory_search_returns_mvp_stub() -> None:
    """MVP returns an empty result with metadata documenting the deferred RPC."""
    drv = OpenClawMemoryDriver({"token": "tok"})
    result = await drv.search("how do I configure Contract B?", scope="all", top_k=5)
    assert result.results == []
    assert result.metadata["status"] == "mvp_stub"
    assert result.metadata["source"] == "openclaw"
    assert result.metadata["query"] == "how do I configure Contract B?"
    assert result.metadata["top_k"] == 5
    assert "v0.2" in result.metadata["implementation_note"]


# ---------------------------------------------------------------------------
# JSON5 comment stripping
# ---------------------------------------------------------------------------


def test_strip_json5_comments_line_comment() -> None:
    raw = '{\n  "a": 1, // inline comment\n  "b": 2\n}'
    cleaned = _strip_json5_comments(raw)
    parsed = json.loads(cleaned)
    assert parsed == {"a": 1, "b": 2}


def test_strip_json5_comments_block_comment() -> None:
    raw = '{ /* multi\nline */ "a": 1, "b": /* inline block */ 2 }'
    cleaned = _strip_json5_comments(raw)
    parsed = json.loads(cleaned)
    assert parsed == {"a": 1, "b": 2}


def test_strip_json5_comments_preserves_strings_with_slashes() -> None:
    raw = '{"url": "https://example.com/foo", "path": "/usr/bin"}'
    cleaned = _strip_json5_comments(raw)
    parsed = json.loads(cleaned)
    assert parsed == {"url": "https://example.com/foo", "path": "/usr/bin"}


def test_strip_json5_comments_does_not_strip_inside_string() -> None:
    raw = '{"note": "use // for comments, not /* not */"}'
    cleaned = _strip_json5_comments(raw)
    parsed = json.loads(cleaned)
    assert parsed == {"note": "use // for comments, not /* not */"}


# ---------------------------------------------------------------------------
# parse_openclaw_config
# ---------------------------------------------------------------------------


def _sample_openclaw_json() -> dict:
    return {
        "gateway": {
            "port": 18789,
            "bind": "loopback",
            "auth": {"mode": "token", "token": "real-token-xyz"},
            "http": {
                "endpoints": {
                    "chatCompletions": {"enabled": True, "requireAuth": True}
                }
            },
        },
        "channels": {"webchat": {}, "telegram": {}},
        "plugins": {"entries": {"minimax": {}, "browser": {}}},
        "agents": {
            "defaults": {
                "memorySearch": {
                    "provider": "openai",
                    "defaultModel": "text-embedding-3-small",
                }
            }
        },
    }


def test_parse_openclaw_config_happy_path() -> None:
    cfg = parse_openclaw_config(_sample_openclaw_json())
    assert cfg.gateway_port == 18789
    assert cfg.gateway_bind == "loopback"
    assert cfg.auth_token == "real-token-xyz"
    assert cfg.has_secret_ref_token is False
    assert cfg.contract_b_enabled is True
    assert cfg.contract_b_require_auth is True
    assert cfg.channels_enabled == ["telegram", "webchat"]
    assert cfg.plugins_enabled == ["browser", "minimax"]
    assert cfg.memory_search_provider == "openai"
    assert cfg.memory_search_default_model == "text-embedding-3-small"
    assert cfg.is_contract_b_ready() is True


def test_parse_openclaw_config_secret_ref() -> None:
    data = _sample_openclaw_json()
    data["gateway"]["auth"]["token"] = SECRET_REF_PLACEHOLDER
    cfg = parse_openclaw_config(data)
    assert cfg.auth_token is None
    assert cfg.has_secret_ref_token is True
    # is_contract_b_ready: contract B is enabled AND has_secret_ref_token=True
    assert cfg.is_contract_b_ready() is True


def test_parse_openclaw_config_contract_b_disabled() -> None:
    data = _sample_openclaw_json()
    data["gateway"]["http"]["endpoints"]["chatCompletions"]["enabled"] = False
    cfg = parse_openclaw_config(data)
    assert cfg.contract_b_enabled is False
    assert cfg.is_contract_b_ready() is False


def test_parse_openclaw_config_handles_missing_sections() -> None:
    cfg = parse_openclaw_config({})
    assert cfg.gateway_port == 18789
    assert cfg.gateway_bind == "loopback"
    assert cfg.auth_token is None
    assert cfg.contract_b_enabled is False
    assert cfg.channels_enabled == []
    assert cfg.plugins_enabled == []


def test_load_openclaw_config_from_real_file(tmp_path: Path) -> None:
    """End-to-end: JSON5 file on disk -> validated config."""
    config_file = tmp_path / "openclaw.json"
    config_file.write_text(
        """
        // AgentOS integration test fixture
        {
          "gateway": {
            "port": 19555, /* non-default to verify parsing */
            "bind": "loopback",
            "auth": { "mode": "token", "token": "fixture-tok" },
            "http": {
              "endpoints": {
                "chatCompletions": { "enabled": true }
              }
            }
          },
          "channels": {
            "webchat": {} // trailing comma after webchat
            ,
          },
          "plugins": { "entries": { "browser": {} } },
        }
        """,
        encoding="utf-8",
    )
    cfg = load_openclaw_config(config_file)
    assert cfg.gateway_port == 19555
    assert cfg.auth_token == "fixture-tok"
    assert cfg.contract_b_enabled is True
    assert cfg.channels_enabled == ["webchat"]
    assert cfg.plugins_enabled == ["browser"]


def test_openclaw_config_contract_b_url() -> None:
    cfg = parse_openclaw_config(_sample_openclaw_json())
    assert cfg.contract_b_url() == "http://127.0.0.1:18789/v1"
    assert cfg.contract_b_url(host="100.64.0.1") == "http://100.64.0.1:18789/v1"
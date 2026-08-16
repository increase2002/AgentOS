"""Tests for resolve_openclaw_token (canonical + fallback paths)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentos.memory.openclaw_token import OpenClawTokenError, resolve_openclaw_token


def test_resolve_from_canonical(tmp_path):
    canonical = tmp_path / "openclaw.json"
    canonical.write_text(
        '{"gateway": {"auth": {"mode": "token", "token": "canonical-123"}}}',
        encoding="utf-8",
    )
    dev = tmp_path / "dev.token"
    assert resolve_openclaw_token(canonical_path=canonical, dev_fallback_path=dev) == "canonical-123"


def test_fallback_to_dev_path(tmp_path):
    canonical = tmp_path / "missing.json"
    dev = tmp_path / "dev.token"
    dev.write_text("dev-fallback-456\n", encoding="utf-8")
    assert resolve_openclaw_token(canonical_path=canonical, dev_fallback_path=dev).strip() == "dev-fallback-456"


def test_canonical_takes_precedence_over_dev(tmp_path):
    canonical = tmp_path / "openclaw.json"
    canonical.write_text('{"gateway": {"auth": {"token": "from-canonical"}}}', encoding="utf-8")
    dev = tmp_path / "dev.token"
    dev.write_text("from-dev", encoding="utf-8")
    assert resolve_openclaw_token(canonical_path=canonical, dev_fallback_path=dev) == "from-canonical"


def test_neither_path_raises(tmp_path):
    canonical = tmp_path / "missing1.json"
    dev = tmp_path / "missing2.token"
    with pytest.raises(OpenClawTokenError, match="No OpenClaw token found"):
        resolve_openclaw_token(canonical_path=canonical, dev_fallback_path=dev)


def test_canonical_with_empty_token_falls_through(tmp_path):
    canonical = tmp_path / "openclaw.json"
    canonical.write_text('{"gateway": {"auth": {}}}', encoding="utf-8")
    dev = tmp_path / "dev.token"
    dev.write_text("dev-after-empty", encoding="utf-8")
    assert resolve_openclaw_token(canonical_path=canonical, dev_fallback_path=dev).strip() == "dev-after-empty"


def test_canonical_with_malformed_json_falls_through(tmp_path):
    canonical = tmp_path / "openclaw.json"
    canonical.write_text("{ this is not valid json", encoding="utf-8")
    dev = tmp_path / "dev.token"
    dev.write_text("dev-after-malformed", encoding="utf-8")
    assert resolve_openclaw_token(canonical_path=canonical, dev_fallback_path=dev).strip() == "dev-after-malformed"


def test_dev_path_with_whitespace_is_stripped(tmp_path):
    canonical = tmp_path / "missing.json"
    dev = tmp_path / "dev.token"
    dev.write_text("  token-with-whitespace  \n", encoding="utf-8")
    result = resolve_openclaw_token(canonical_path=canonical, dev_fallback_path=dev)
    assert result == "token-with-whitespace"

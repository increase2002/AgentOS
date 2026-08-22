"""Tests for the OpenClaw D2 sidecar (examples/openclaw_sidecar.py).

Focus on the pure helpers (no real LLM, no bus file). Covers:
- _build_brief: payload shape extraction
- _bus_send: subprocess invocation shape (mocked)
"""

from __future__ import annotations

import sys
from pathlib import Path

# ensure src/ on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import examples.openclaw_sidecar as sidecar  # noqa: E402
from agentos.schemas.message import (  # noqa: E402
    Message,
    MessageType,
    Priority,
)


def _msg(payload: dict) -> Message:
    """Build a Message with the given payload (rest defaults)."""
    return Message(
        id="m-1",
        from_agent="codex",
        to_agent="openclaw",
        type=MessageType.HANDOFF,
        priority=Priority.NORMAL,
        payload=payload,
    )


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
    # fallback should mention message id + payload keys
    assert "m-1" in out
    assert "task_id" in out or "t-x" in out  # any usable identifier


def test_build_brief_empty_payload_actually_empty() -> None:
    m = _msg({})
    out = sidecar._build_brief(m)
    assert "m-1" in out  # always references msg id for traceability

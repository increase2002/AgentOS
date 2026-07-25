"""Smoke tests for A2A schemas."""

from __future__ import annotations

import pytest

from agentos.schemas import (
    Artifact,
    ArtifactFile,
    Message,
    MessageType,
    Priority,
)
from agentos.schemas.a2a import build_session_key


def test_artifact_minimal() -> None:
    art = Artifact(
        task_id="t-1",
        stage="research",
        producing_agent="codex",
        artifact_type="research_report",
        summary="Identified 3 candidate architectures.",
    )
    assert art.schema_version == "0.1"
    assert art.task_id == "t-1"
    assert art.files == []
    dumped = art.model_dump_json()
    assert "research_report" in dumped


def test_artifact_with_files_and_metadata() -> None:
    art = Artifact(
        task_id="t-1",
        stage="code",
        producing_agent="codex",
        artifact_type="pr_diff",
        summary="Implemented feature X with 3 new modules.",
        files=[ArtifactFile(path="src/foo.py", mime="text/x-python")],
        open_questions=["Should we add a migration?"],
        producer_metadata={
            "model_version": "gpt-5",
            "token_input": 1234,
            "token_output": 567,
            "latency_ms": 4200,
        },
    )
    assert art.producer_metadata["model_version"] == "gpt-5"
    assert len(art.files) == 1


def test_message_serialization() -> None:
    msg = Message(
        id="m-1",
        from_agent="codex",
        to_agent="openclaw",
        type=MessageType.TASK_REQUEST,
        priority=Priority.HIGH,
        payload={"brief": "deploy this"},
    )
    assert msg.from_agent == "codex"
    assert msg.to_agent == "openclaw"
    assert msg.type == MessageType.TASK_REQUEST


def test_session_key_template() -> None:
    key = build_session_key("t-abc", "research")
    assert key == "task:t-abc:stage:research"


def test_session_key_with_sub() -> None:
    key = build_session_key("t-abc", "code", sub_id="worker-1")
    assert key == "task:t-abc:stage:code:sub:worker-1"


def test_session_key_length_limit() -> None:
    with pytest.raises(ValueError, match="too long"):
        build_session_key("t-" + "x" * 200, "stage")
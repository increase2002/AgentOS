"""Artifact schema - structured work product passed between stages.

Cross-stage handoff uses Artifact, NOT conversation history. Conversation
history is never passed between stages (token cost).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ArtifactFile(BaseModel):
    """A single file attached to an artifact."""

    path: str
    mime: str
    content: str | None = None  # inlined for small files
    size: int | None = None


class Artifact(BaseModel):
    """Structured output produced by an agent stage."""

    schema_version: str = Field(
        default="0.1",
        description="Schema version - increment when breaking the contract",
    )
    task_id: str
    stage: str
    producing_agent: str
    artifact_type: str = Field(
        description="e.g. research_report, pr_diff, deploy_log, test_report"
    )
    files: list[ArtifactFile] = Field(default_factory=list)
    summary: str = Field(description="Human-readable summary for downstream agents")
    open_questions: list[str] = Field(default_factory=list)
    next_stage_inputs: dict[str, Any] = Field(default_factory=dict)
    producer_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "model_version, temperature, token_input, token_output, latency_ms - "
            "lets evaluation tie cost directly to the artifact that produced it"
        ),
    )
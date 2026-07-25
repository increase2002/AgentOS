"""Task DAG schema — output of the Planner sub-agent.

The Planner LLM emits a TaskDAG that the Orchestrator Engine executes.
Validation happens at the boundary; invalid DAGs trigger Planner
refinement retry (up to N attempts) before degrading to single-agent
fallback (per ADR-0003 + Orchestrator Engine ADR-0010).

Refs: ADR-0003 (Sub-Agents), ADR-0009 (tool_subset),
ADR-0010 (Orchestrator Engine, in progress by OpenClaw).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator


# Recognized agent identifiers in v0.1. Driver registry adds more.
AGENT_CHOICES: tuple[str, ...] = (
    "openclaw",   # OpenClaw Contract B / Contract A
    "codex",      # CodexAdapter (CLI subprocess wrapper)
    "claude",     # AnthropicDriver (Messages -> OpenAI-compat)
    "gemini",     # GeminiDriver (Google OpenAI-compat)
    "judge",      # internal sub-agent (ADR-0003)
    "summarizer", # internal sub-agent (ADR-0003)
    "router",     # internal sub-agent (ADR-0003, code-only)
    "cost",       # internal sub-agent (ADR-0003, code-only)
)


class DAGNode(BaseModel):
    """One stage in the task DAG.

    A node represents a single driver invocation. Inputs reference other
    nodes by stage_id; the Orchestrator Engine resolves these into
    Artifact references at execution time.
    """

    stage_id: str = Field(
        description="Unique identifier within the DAG (e.g. 'research', 'code', 'review').",
        min_length=1,
        max_length=64,
    )
    agent: str = Field(
        description=(
            "Which driver / sub-agent runs this stage. Must be one of AGENT_CHOICES "
            "or a value registered at runtime via DriverRegistry."
        ),
    )
    brief_template: str = Field(
        description=(
            "f-string template for the task brief. Receives upstream artifact fields "
            "as kwargs (e.g. {research_summary}). Filled by Orchestrator before "
            "driver.chat() is called."
        ),
        min_length=1,
    )
    inputs: list[str] = Field(
        default_factory=list,
        description="stage_ids whose artifacts this node consumes.",
    )
    parallel_group: Optional[int] = Field(
        default=None,
        description=(
            "Same non-None group => parallel dispatch under the Concurrency Budget "
            "(ADR-0006). None => sequential after all inputs complete."
        ),
    )
    expected_artifact_type: str = Field(
        description=(
            "Artifact schema type this stage is expected to produce "
            "(e.g. 'research_report', 'pr_diff', 'deploy_log')."
        ),
    )
    debate_eligible: bool = Field(
        default=False,
        description=(
            "If True, this stage may use Debate mode (multiple agents propose, Judge "
            "picks) per ADR-0004. Only valid for decision-class tasks; "
            "Planner must justify the flag in its prompt."
        ),
    )
    dag_cache_key: Optional[str] = Field(
        default=None,
        description=(
            "If set and Orchestrator has a cached artifact for this key, skip "
            "execution and reuse the prior artifact. Hash the task brief + agent "
            "+ inputs to compute a stable key."
        ),
    )
    tool_subset: Optional[list[str]] = Field(
        default=None,
        description=(
            "If set, restrict the agent's tool access per ADR-0009. Empty list = "
            "plan-only / read-only mode."
        ),
    )

    @model_validator(mode="after")
    def _check_agent_known(self) -> "DAGNode":
        # Soft check: warn if agent is not in known list (don't hard-fail,
        # because DriverRegistry may add custom agents at runtime).
        if self.agent not in AGENT_CHOICES:
            # Surface as a warning, not a validation error.
            import warnings
            warnings.warn(
                f"DAGNode.stage_id={self.stage_id!r} uses unknown agent "
                f"{self.agent!r}; expected one of {AGENT_CHOICES} or a "
                f"runtime-registered agent.",
                stacklevel=2,
            )
        return self


class TaskDAG(BaseModel):
    """Top-level DAG emitted by the Planner LLM.

    Validation:
    - All node stage_ids must be unique.
    - All node inputs must reference an existing stage_id.
    - parallel_group values must be non-negative integers.
    """

    task_id: str = Field(min_length=1)
    nodes: list[DAGNode] = Field(min_length=1)
    dag_cache_key: Optional[str] = Field(
        default=None,
        description="Cache key for the entire DAG; if hit, skip Planner for this task.",
    )

    @model_validator(mode="after")
    def _validate_dag(self) -> "TaskDAG":
        stage_ids = {n.stage_id for n in self.nodes}
        if len(stage_ids) != len(self.nodes):
            dupes = [sid for sid in stage_ids if sum(1 for n in self.nodes if n.stage_id == sid) > 1]
            raise ValueError(f"duplicate stage_id in DAG: {dupes}")

        for node in self.nodes:
            missing = [i for i in node.inputs if i not in stage_ids]
            if missing:
                raise ValueError(
                    f"node {node.stage_id!r} references missing inputs: {missing}"
                )
            if node.parallel_group is not None and node.parallel_group < 0:
                raise ValueError(
                    f"node {node.stage_id!r} has negative parallel_group: {node.parallel_group}"
                )

        return self
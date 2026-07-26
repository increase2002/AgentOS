"""Planner sub-agent (ADR-0003, ADR-0010).

The Planner turns a free-form task brief into a :class:`TaskDAG` that the
Orchestrator Engine can execute. In v0.1 the Planner is a thin wrapper
around a caller-supplied DAG; the LLM-driven version (Planner LLM call +
schema validation + retry) lands in v0.2 per the Q-E agreement.

Usage
-----

Pre-built DAG (v0.1, works today)::

    from agentos.internal_agents import predefined_dag
    dag = predefined_dag(
        task_id="t-001",
        stages=[
            {"stage_id": "research", "agent": "openclaw",
             "brief": "Research {task_brief}", "artifact_type": "research_report"},
            {"stage_id": "write", "agent": "codex",
             "brief": "Write a draft based on: {research}",
             "artifact_type": "draft", "depends_on": ["research"]},
        ],
    )
    await engine.run(task_id="t-001", dag_payload=dag.model_dump())

Future v0.2 LLM-driven planner::

    planner = Planner(llm_driver=openclaw_driver)
    dag = await planner.plan(brief="...", dag_cache_key=None)
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from agentos.schemas.dag import DAGNode, TaskDAG

if TYPE_CHECKING:  # pragma: no cover
    from agentos.drivers.base import BaseDriver


class PlannerError(Exception):
    """Raised when the Planner cannot produce a valid DAG."""


class Planner:
    """LLM-driven Planner (v0.2). v0.1: use ``predefined_dag`` instead."""

    def __init__(self, llm_driver: "BaseDriver", max_retries: int = 3) -> None:
        self.llm_driver = llm_driver
        self.max_retries = max_retries

    async def plan(
        self,
        *,
        brief: str,
        dag_cache_key: str | None = None,
    ) -> TaskDAG:
        """Ask the LLM to produce a TaskDAG, retrying on validation failure.

        TODO v0.2:
        1. Build a Planner prompt: brief + examples + JSON schema for TaskDAG.
        2. Call ``await llm_driver.chat(prompt, tool_subset=["json"])``.
        3. Parse JSON -> ``TaskDAG.model_validate``.
        4. On validation error, retry with the error appended to prompt.
        5. After max_retries, degrade to single-agent fallback (per ADR-0003).
        """
        raise NotImplementedError(
            "LLM-driven Planner lands in v0.2; use predefined_dag() for now."
        )


def predefined_dag(
    *,
    task_id: str,
    stages: list[dict[str, Any]],
    dag_cache_key: str | None = None,
) -> TaskDAG:
    """Build a TaskDAG from a list of stage specs.

    Each stage spec is a dict with keys:
        stage_id (str, required)
        agent (str, required) — must match a driver name
        brief (str, required) — f-string template (sees ``{task_brief}``
            and ``{<upstream_stage_id>}`` for upstream content)
        artifact_type (str, required)
        depends_on (list[str], optional) — upstream stage_ids
        parallel_group (int, optional) — non-None => parallel dispatch
        tool_subset (list[str], optional) — per ADR-0009
        debate_eligible (bool, optional, default False)
        dag_cache_key (str, optional) — skip if cached
    """
    nodes: list[DAGNode] = []
    for spec in stages:
        stage_id = spec["stage_id"]
        depends = spec.get("depends_on", [])
        node = DAGNode(
            stage_id=stage_id,
            agent=spec["agent"],
            brief_template=spec["brief"],
            inputs=list(depends),
            parallel_group=spec.get("parallel_group"),
            expected_artifact_type=spec["artifact_type"],
            tool_subset=spec.get("tool_subset"),
            debate_eligible=spec.get("debate_eligible", False),
            dag_cache_key=spec.get("dag_cache_key"),
        )
        nodes.append(node)
    return TaskDAG(task_id=task_id, nodes=nodes, dag_cache_key=dag_cache_key)


__all__ = ["Planner", "PlannerError", "predefined_dag"]
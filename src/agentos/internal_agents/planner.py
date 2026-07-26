"""Planner sub-agent (ADR-0003, ADR-0010).

The Planner turns a free-form task brief into a :class:`TaskDAG` that the
Orchestrator Engine can execute.

v0.1: ``predefined_dag()`` builds a DAG from a stage-spec list (no LLM).
v0.2: ``Planner.plan()`` uses a small LLM (gpt-4o-mini / gemini-2.0-flash)
to generate the DAG, with 3-retry validation + single-agent fallback.

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

LLM-driven DAG (v0.2)::

    planner = Planner(llm_driver=openclaw_driver, max_retries=3)
    dag = await planner.plan(
        brief="Build a research report on agent interoperability",
        agent_list=["openclaw", "codex", "claude"],
        dag_cache_key="research-v1",
    )
"""

from __future__ import annotations

import json
import logging
from typing import Any, TYPE_CHECKING

from agentos.schemas.dag import DAGNode, TaskDAG

if TYPE_CHECKING:  # pragma: no cover
    from agentos.drivers.base import BaseDriver

logger = logging.getLogger(__name__)

# Prompt template. {agent_list} is substituted at runtime.
PLANNER_SYSTEM_PROMPT = """You are a Planner sub-agent in AgentOS. Given a task brief, produce a TaskDAG that the Orchestrator Engine will execute.

Available agents: {agent_list}

Output format (JSON only, no prose, no code fences):
{{
  "task_id": "<uuid or brief-derived>",
  "dag_cache_key": "<optional cache key>",
  "nodes": [
    {{
      "stage_id": "<snake_case_unique>",
      "agent": "<one of available agents>",
      "brief_template": "<f-string; use {{{{task_brief}}}} and {{{{upstream_stage_id}}}} refs>",
      "inputs": ["<upstream_stage_id>", ...],
      "parallel_group": <int or null>,
      "expected_artifact_type": "<snake_case>",
      "debate_eligible": <bool>,
      "dag_cache_key": "<optional>",
      "tool_subset": <list[str] or null>
    }},
    ...
  ]
}}

Rules:
- Stage IDs unique and snake_case.
- All `inputs` must reference an existing stage_id (cannot reference self).
- `brief_template` is an f-string; use {{task_brief}} for the task and {{upstream_stage_id}} to reference upstream content.
- For tasks with no clear multi-stage decomposition, output a SINGLE node (do not over-decompose).
- `debate_eligible: true` ONLY for: tech-stack choice / architecture decision / creative content. NOT for code, test, deploy, retrieval, data analysis.
- `tool_subset: []` means plan-only / read-only (per ADR-0009). Use for early stages if no write access needed.
- `tool_subset: ["read_file", "grep"]` means only those tools allowed.
"""


class PlannerError(Exception):
    """Raised when the Planner cannot produce a valid DAG."""


class Planner:
    """LLM-driven Planner (v0.2)."""

    DEFAULT_AGENTS = ("openclaw", "codex", "claude", "gemini")
    FALLBACK_AGENT = "openclaw"

    def __init__(
        self,
        llm_driver: "BaseDriver",
        *,
        max_retries: int = 3,
        fallback_agent: str = FALLBACK_AGENT,
    ) -> None:
        self.llm_driver = llm_driver
        self.max_retries = max_retries
        self.fallback_agent = fallback_agent
        self._cache: dict[str, TaskDAG] = {}

    async def plan(
        self,
        *,
        brief: str,
        agent_list: list[str] | tuple[str, ...] | None = None,
        dag_cache_key: str | None = None,
        task_id: str | None = None,
    ) -> TaskDAG:
        """Ask the LLM to produce a TaskDAG, retrying on validation failure.

        Falls back to a single-agent DAG after max_retries (per ADR-0003
        + Codex Q-E).
        """
        if not brief or not brief.strip():
            raise PlannerError("brief must be non-empty")

        # Cache hit
        if dag_cache_key and dag_cache_key in self._cache:
            logger.info("planner: cache hit for %s", dag_cache_key)
            return self._cache[dag_cache_key]

        agents = list(agent_list) if agent_list else list(self.DEFAULT_AGENTS)
        system_prompt = PLANNER_SYSTEM_PROMPT.format(agent_list=", ".join(agents))

        last_error: str | None = None
        for attempt in range(1, self.max_retries + 1):
            user_msg = f"Task brief:\n{brief}"
            if last_error is not None:
                user_msg += (
                    f"\n\nPrevious attempt failed with this error:\n{last_error}\n\n"
                    f"Fix the JSON and retry."
                )
            full_prompt = system_prompt + "\n\n" + user_msg
            try:
                response = await self.llm_driver.chat(
                    full_prompt,
                    tool_subset=["json"],  # hint: produce JSON only
                )
                dag = self._parse_and_validate(response.content, task_id=task_id)
                if dag_cache_key:
                    self._cache[dag_cache_key] = dag
                logger.info(
                    "planner: produced DAG with %d node(s) on attempt %d",
                    len(dag.nodes), attempt,
                )
                return dag
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "planner: attempt %d/%d failed: %s",
                    attempt, self.max_retries, last_error,
                )
                continue
            except Exception as exc:
                # Network / driver error: surface, do not retry blindly.
                logger.error("planner: driver error: %s", exc)
                raise PlannerError(f"Planner driver error: {exc}") from exc

        # Fallback: single-agent DAG
        logger.warning(
            "planner: all %d attempts failed; degrading to single-agent fallback",
            self.max_retries,
        )
        return self._single_agent_fallback(brief, agents, task_id=task_id)

    def _parse_and_validate(self, text: str, *, task_id: str | None = None) -> TaskDAG:
        # Strip optional markdown code fences
        s = text.strip()
        if s.startswith("```"):
            lines = s.split("\n")
            # drop first line (```json or ```) and last line (```)
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            s = "\n".join(lines[1:]) if len(lines) > 1 else ""
        s = s.strip()
        data = json.loads(s)
        # If task_id was provided by caller and LLM omitted it, inject.
        if task_id and "task_id" not in data:
            data["task_id"] = task_id
        return TaskDAG.model_validate(data)

    def _single_agent_fallback(
        self,
        brief: str,
        agents: list[str],
        *,
        task_id: str | None = None,
    ) -> TaskDAG:
        """Degrade to a single-node DAG using the fallback agent."""
        agent = self.fallback_agent
        if agents and agent not in agents:
            agent = agents[0]
        return TaskDAG(
            task_id=task_id or "fallback-task",
            nodes=[
                DAGNode(
                    stage_id="single",
                    agent=agent,
                    brief_template="{task_brief}",
                    expected_artifact_type="artifact",
                ),
            ],
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
            debate_eligible=spec.get("debate_eligible", False),
            dag_cache_key=spec.get("dag_cache_key"),
            tool_subset=spec.get("tool_subset"),
        )
        nodes.append(node)
    return TaskDAG(
        task_id=task_id,
        nodes=nodes,
        dag_cache_key=dag_cache_key,
    )
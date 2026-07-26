"""Tests for the LLM-driven Planner (v0.2)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from agentos.drivers.base import ChatResult
from agentos.internal_agents.planner import (
    Planner,
    PlannerError,
    predefined_dag,
)
from agentos.schemas.dag import DAGNode, TaskDAG


class FakeDriver:
    """Minimal driver stub that returns preset responses in sequence."""

    def __init__(self, responses: list[str], *, tool_subsets_received: list | None = None):
        self._responses = list(responses)
        self._calls: list[dict[str, Any]] = []
        self._tool_subsets_received = tool_subsets_received

    async def chat(self, brief, *, attachments=None, session_key=None, tool_subset=None):
        self._calls.append({
            "brief": brief,
            "tool_subset": tool_subset,
            "attachments": attachments,
            "session_key": session_key,
        })
        if self._tool_subsets_received is not None and tool_subset is not None:
            self._tool_subsets_received.append(tool_subset)
        if not self._responses:
            raise PlannerError("FakeDriver ran out of responses")
        content = self._responses.pop(0)
        return ChatResult(content=content, usage=None, metadata={"tool_subset": tool_subset})

    @property
    def calls(self):
        return self._calls


def _valid_dag_json() -> str:
    return json.dumps({
        "task_id": "t-001",
        "nodes": [
            {
                "stage_id": "research",
                "agent": "openclaw",
                "brief_template": "Research {task_brief}",
                "expected_artifact_type": "research_report",
            },
            {
                "stage_id": "write",
                "agent": "codex",
                "brief_template": "Write based on {research}",
                "inputs": ["research"],
                "expected_artifact_type": "draft",
            },
        ],
    })


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_first_attempt_succeeds() -> None:
    driver = FakeDriver([_valid_dag_json()])
    planner = Planner(driver)

    dag = await planner.plan(brief="Build a research report")

    assert isinstance(dag, TaskDAG)
    assert dag.task_id == "t-001"
    assert len(dag.nodes) == 2
    assert dag.nodes[0].stage_id == "research"
    assert dag.nodes[1].inputs == ["research"]
    assert len(driver.calls) == 1


@pytest.mark.asyncio
async def test_plan_strips_markdown_code_fences() -> None:
    driver = FakeDriver(["```json\n" + _valid_dag_json() + "\n```"])
    planner = Planner(driver)
    dag = await planner.plan(brief="x")
    assert dag.task_id == "t-001"


@pytest.mark.asyncio
async def test_plan_uses_tool_subset_for_json() -> None:
    """Planner should request tool_subset=['json'] to hint the LLM."""
    tool_subsets = []
    driver = FakeDriver([_valid_dag_json()], tool_subsets_received=tool_subsets)
    planner = Planner(driver)
    await planner.plan(brief="x")
    assert tool_subsets == [["json"]]


# ---------------------------------------------------------------------------
# Retry on validation failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_retries_on_invalid_json() -> None:
    """Bad JSON -> error -> retry -> success."""
    driver = FakeDriver([
        "this is not json",
        _valid_dag_json(),
    ])
    planner = Planner(driver, max_retries=3)
    dag = await planner.plan(brief="x")
    assert dag.task_id == "t-001"
    assert len(driver.calls) == 2
    # Second call should contain error feedback from first failure
    assert "Previous attempt failed" in driver.calls[1]["brief"]


@pytest.mark.asyncio
async def test_plan_retries_on_schema_validation_error() -> None:
    """JSON parses but fails TaskDAG validation -> retry."""
    bad_schema = json.dumps({
        "task_id": "t-001",
        "nodes": [
            {
                "stage_id": "only",
                "agent": "openclaw",
                "brief_template": "x",
                "inputs": ["nonexistent"],  # references missing stage_id
                "expected_artifact_type": "r",
            },
        ],
    })
    driver = FakeDriver([bad_schema, _valid_dag_json()])
    planner = Planner(driver, max_retries=3)
    dag = await planner.plan(brief="x")
    assert len(dag.nodes) == 2
    assert len(driver.calls) == 2


# ---------------------------------------------------------------------------
# Fallback after max retries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_falls_back_to_single_agent_after_max_retries() -> None:
    """3 bad responses -> fallback DAG with one node."""
    driver = FakeDriver([
        "garbage 1",
        "garbage 2",
        "garbage 3",
    ])
    planner = Planner(driver, max_retries=3)
    dag = await planner.plan(brief="x")
    assert isinstance(dag, TaskDAG)
    assert len(dag.nodes) == 1
    assert dag.nodes[0].stage_id == "single"
    assert dag.nodes[0].agent == planner.fallback_agent


@pytest.mark.asyncio
async def test_plan_fallback_picks_agent_from_agent_list() -> None:
    """Fallback uses first available agent if fallback_agent not in list."""
    driver = FakeDriver(["bad"] * 3)
    planner = Planner(driver, max_retries=3, fallback_agent="nonexistent")
    dag = await planner.plan(brief="x", agent_list=["codex", "claude"])
    assert dag.nodes[0].agent == "codex"


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_cache_hit_skips_llm_call() -> None:
    """Same dag_cache_key on second call returns cached DAG without re-querying."""
    driver = FakeDriver([_valid_dag_json()])
    planner = Planner(driver)

    dag1 = await planner.plan(brief="x", dag_cache_key="research-v1")
    dag2 = await planner.plan(brief="x", dag_cache_key="research-v1")

    assert dag1 is dag2  # same object (cached)
    assert len(driver.calls) == 1  # LLM called only once


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_empty_brief_raises() -> None:
    driver = FakeDriver([])
    planner = Planner(driver)
    with pytest.raises(PlannerError, match="non-empty"):
        await planner.plan(brief="")


@pytest.mark.asyncio
async def test_plan_propagates_driver_errors() -> None:
    """Driver raises -> Planner raises PlannerError (after marking)."""

    class BrokenDriver:
        async def chat(self, *args, **kwargs):
            raise RuntimeError("network down")

    planner = Planner(BrokenDriver(), max_retries=2)
    with pytest.raises(PlannerError, match="driver error"):
        await planner.plan(brief="x")


@pytest.mark.asyncio
async def test_plan_injects_task_id_when_missing() -> None:
    """If LLM omits task_id but caller provides one, inject it."""
    no_task_id = json.dumps({
        "nodes": [
            {
                "stage_id": "only",
                "agent": "openclaw",
                "brief_template": "x",
                "expected_artifact_type": "r",
            },
        ],
    })
    driver = FakeDriver([no_task_id])
    planner = Planner(driver)
    dag = await planner.plan(brief="x", task_id="my-task")
    assert dag.task_id == "my-task"


# ---------------------------------------------------------------------------
# predefined_dag (v0.1)
# ---------------------------------------------------------------------------


def test_predefined_dag_basic() -> None:
    dag = predefined_dag(
        task_id="t-001",
        stages=[
            {
                "stage_id": "research",
                "agent": "openclaw",
                "brief": "Research {task_brief}",
                "artifact_type": "research_report",
            },
            {
                "stage_id": "write",
                "agent": "codex",
                "brief": "Write based on {research}",
                "artifact_type": "draft",
                "depends_on": ["research"],
            },
        ],
    )
    assert dag.task_id == "t-001"
    assert len(dag.nodes) == 2
    assert dag.nodes[1].inputs == ["research"]


def test_predefined_dag_with_parallel_group_and_tool_subset() -> None:
    dag = predefined_dag(
        task_id="t-002",
        stages=[
            {
                "stage_id": "scan",
                "agent": "openclaw",
                "brief": "Scan {task_brief}",
                "artifact_type": "scan_result",
                "tool_subset": ["read_file", "grep"],
            },
            {
                "stage_id": "analyze",
                "agent": "codex",
                "brief": "Analyze scan",
                "artifact_type": "analysis",
                "depends_on": ["scan"],
                "parallel_group": 1,
            },
        ],
    )
    assert dag.nodes[0].tool_subset == ["read_file", "grep"]
    assert dag.nodes[1].parallel_group == 1
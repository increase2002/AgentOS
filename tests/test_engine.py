"""Tests for the Engine public API."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from agentos.drivers.base import BaseDriver, ChatResult, DriverError
from agentos.orchestrator.engine import (
    Engine,
    OrchestratorError,
    TaskResult,
    UnknownAgentError,
)
from agentos.orchestrator.checkpoint import TaskStatus
from agentos.schemas.dag import DAGNode, TaskDAG


class FakeDriver(BaseDriver):
    def __init__(self, name: str, config: dict[str, Any] | None = None):
        super().__init__(name, config or {})
        self.calls: list[str] = []

    async def chat(self, brief, *, attachments=None, session_key=None, tool_subset=None):
        self.calls.append(session_key or "")
        return ChatResult(content=f"reply: {brief}", usage={"total_tokens": 5})

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    openclaw = FakeDriver("openclaw")
    codex = FakeDriver("codex")
    return Engine(
        drivers={"openclaw": openclaw, "codex": codex},
        checkpoint_dir=tmp_path / "checkpoints",
        concurrency_budget=4,
    )


@pytest.mark.asyncio
async def test_engine_run_simple_dag(engine: Engine):
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(stage_id="research", agent="openclaw", brief_template="r", expected_artifact_type="t"),
            DAGNode(stage_id="write", agent="codex", brief_template="w based on {research}", inputs=["research"], expected_artifact_type="t"),
        ],
    )
    result = await engine.run(task_id="t-1", dag_payload=dag)
    assert result.status == TaskStatus.COMPLETED
    assert "research" in result.stages
    assert "write" in result.stages


@pytest.mark.asyncio
async def test_engine_aggregates_cost(engine: Engine):
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(stage_id="a", agent="openclaw", brief_template="a", expected_artifact_type="t"),
            DAGNode(stage_id="b", agent="codex", brief_template="b", inputs=["a"], expected_artifact_type="t"),
        ],
    )
    result = await engine.run(task_id="t-1", dag_payload=dag)
    assert result.total_cost.get("total_tokens", 0) == 10  # 5 + 5


@pytest.mark.asyncio
async def test_engine_register_driver_at_runtime(engine: Engine):
    claude = FakeDriver("claude")
    engine.register_driver("claude", claude)
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(stage_id="a", agent="claude", brief_template="a", expected_artifact_type="t"),
        ],
    )
    result = await engine.run(task_id="t-1", dag_payload=dag)
    assert result.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_engine_unknown_agent_raises(engine: Engine):
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(stage_id="a", agent="claude", brief_template="a", expected_artifact_type="t"),
        ],
    )
    with pytest.raises(UnknownAgentError):
        await engine.run(task_id="t-1", dag_payload=dag)


@pytest.mark.asyncio
async def test_engine_rejects_missing_dag(engine: Engine):
    with pytest.raises(OrchestratorError):
        await engine.run(task_id="t-1", dag_payload=None)


@pytest.mark.asyncio
async def test_engine_accepts_dict_dag(engine: Engine):
    dag_dict = {
        "task_id": "t-1",
        "nodes": [
            {"stage_id": "a", "agent": "openclaw",
             "brief_template": "do a", "expected_artifact_type": "t"},
        ],
    }
    result = await engine.run(task_id="t-1", dag_payload=dag_dict)
    assert result.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_engine_get_checkpoint(engine: Engine):
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(stage_id="a", agent="openclaw", brief_template="a", expected_artifact_type="t"),
        ],
    )
    await engine.run(task_id="t-1", dag_payload=dag)
    cp = engine.get_checkpoint("t-1")
    assert cp is not None
    assert cp.task_id == "t-1"
    assert "a" in cp.stages


def test_engine_requires_drivers():
    with pytest.raises(OrchestratorError):
        Engine(drivers={})


@pytest.mark.asyncio
async def test_engine_partial_success_replay(engine: Engine):
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(stage_id="a", agent="openclaw", brief_template="a", expected_artifact_type="t"),
            DAGNode(stage_id="b", agent="codex", brief_template="b", inputs=["a"], expected_artifact_type="t"),
        ],
    )
    result1 = await engine.run(task_id="t-1", dag_payload=dag)
    assert result1.status == TaskStatus.COMPLETED

    # Now mutate driver: should still succeed (re-run from checkpoint; 'a' skipped).
    result2 = await engine.run(task_id="t-1", dag_payload=dag)
    assert result2.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_engine_force_rerun(engine: Engine):
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(stage_id="a", agent="openclaw", brief_template="a", expected_artifact_type="t"),
        ],
    )
    await engine.run(task_id="t-1", dag_payload=dag)
    # Force re-run despite checkpoint.
    result = await engine.run(task_id="t-1", dag_payload=dag, force_rerun=True)
    assert result.status == TaskStatus.COMPLETED
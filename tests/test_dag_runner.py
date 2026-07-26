"""Tests for DAGRunner + plan_waves."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from agentos.drivers.base import BaseDriver, ChatResult, DriverError
from agentos.orchestrator.checkpoint import TaskCheckpointStore
from agentos.orchestrator.dag_runner import (
    DAGRunner,
    StageExecutionError,
    Wave,
    plan_waves,
)
from agentos.schemas.dag import DAGNode, TaskDAG
from agentos.telemetry import JSONLHook


# --------------------------------------------------------------------------- #
# Fake drivers
# --------------------------------------------------------------------------- #


class FakeDriver(BaseDriver):
    """Sync fake driver for testing."""

    def __init__(self, name: str, config: dict[str, Any] | None = None):
        super().__init__(name, config or {})
        self.calls: list[dict[str, Any]] = []
        self.responses: dict[str, ChatResult] = {}
        self.fail_on: set[str] = set()
        self.delay_s: float = 0.0

    async def chat(
        self,
        brief,
        *,
        attachments=None,
        session_key=None,
        tool_subset=None,
    ):
        self.calls.append({
            "brief": brief,
            "session_key": session_key,
            "tool_subset": tool_subset,
        })
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if session_key in self.fail_on:
            raise DriverError(f"driver failed for {session_key}")
        if session_key in self.responses:
            return self.responses[session_key]
        # Default: echo back the brief with a marker.
        return ChatResult(
            content=f"reply to: {brief}",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


# --------------------------------------------------------------------------- #
# plan_waves
# --------------------------------------------------------------------------- #


def test_plan_waves_simple_sequential():
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(stage_id="a", agent="x", brief_template="do a", expected_artifact_type="t"),
            DAGNode(stage_id="b", agent="x", brief_template="do b", inputs=["a"], expected_artifact_type="t"),
            DAGNode(stage_id="c", agent="x", brief_template="do c", inputs=["b"], expected_artifact_type="t"),
        ],
    )
    waves = plan_waves(dag)
    assert len(waves) == 3
    assert [w.nodes[0].stage_id for w in waves] == ["a", "b", "c"]


def test_plan_waves_parallel():
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(stage_id="research", agent="x", brief_template="r", expected_artifact_type="t"),
            DAGNode(stage_id="fetch-a", agent="x", brief_template="fa", inputs=["research"], expected_artifact_type="t", parallel_group=1),
            DAGNode(stage_id="fetch-b", agent="x", brief_template="fb", inputs=["research"], expected_artifact_type="t", parallel_group=1),
            DAGNode(stage_id="synth", agent="x", brief_template="s", inputs=["fetch-a", "fetch-b"], expected_artifact_type="t"),
        ],
    )
    waves = plan_waves(dag)
    assert len(waves) == 3
    # Wave 0: research (no deps).
    assert [n.stage_id for n in waves[0].nodes] == ["research"]
    # Wave 1: fetch-a + fetch-b (parallel).
    assert {n.stage_id for n in waves[1].nodes} == {"fetch-a", "fetch-b"}
    # Wave 2: synth.
    assert [n.stage_id for n in waves[2].nodes] == ["synth"]


def test_plan_waves_cyclic_raises():
    """plan_waves() detects cycles at execution time (TaskDAG schema
    only validates input references, not cycles)."""
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(stage_id="a", agent="x", brief_template="a", inputs=["b"], expected_artifact_type="t"),
            DAGNode(stage_id="b", agent="x", brief_template="b", inputs=["a"], expected_artifact_type="t"),
        ],
    )
    with pytest.raises(ValueError, match="cyclic"):
        plan_waves(dag)


def test_plan_waves_duplicate_stage_ids_raises():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TaskDAG(
            task_id="t-1",
            nodes=[
                DAGNode(stage_id="a", agent="x", brief_template="a", expected_artifact_type="t"),
                DAGNode(stage_id="a", agent="x", brief_template="a2", expected_artifact_type="t"),
            ],
        )


# --------------------------------------------------------------------------- #
# DAGRunner end-to-end
# --------------------------------------------------------------------------- #


def _make_runner(
    drivers: dict[str, FakeDriver],
    tmp_path: Path,
    *,
    concurrency_budget: int = 4,
    retry_attempts: int = 1,
) -> DAGRunner:
    return DAGRunner(
        resolve_driver=lambda name: drivers[name],
        checkpoint_store=TaskCheckpointStore(base_dir=tmp_path / "checkpoints"),
        telemetry=JSONLHook(base_dir=tmp_path / "telemetry", enabled=False),
        concurrency_budget=concurrency_budget,
        retry_attempts=retry_attempts,
    )


@pytest.mark.asyncio
async def test_dag_runner_runs_simple_dag(tmp_path: Path):
    driver = FakeDriver("x")
    runner = _make_runner({"x": driver}, tmp_path)
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(stage_id="a", agent="x", brief_template="do a", expected_artifact_type="t"),
        ],
    )
    results = await runner.run(dag)
    assert "a" in results
    assert results["a"].content == "reply to: do a"
    assert driver.calls[0]["session_key"] == "task:t-1:stage:a"


@pytest.mark.asyncio
async def test_dag_runner_chains_stages(tmp_path: Path):
    driver = FakeDriver("x")
    runner = _make_runner({"x": driver}, tmp_path)
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(stage_id="research", agent="x", brief_template="research", expected_artifact_type="t"),
            DAGNode(stage_id="write", agent="x", brief_template="write based on {research}", inputs=["research"], expected_artifact_type="t"),
        ],
    )
    results = await runner.run(dag)
    assert results["write"].content.startswith("reply to: write based on reply to: research")


@pytest.mark.asyncio
async def test_dag_runner_partial_success_replay(tmp_path: Path):
    """Re-running a task should skip completed stages and only retry failed ones."""
    call_count_per_stage: dict[str, int] = {"a": 0, "b": 0}
    fail_b_once = {"flag": True}

    class CountingDriver(FakeDriver):
        async def chat(self, brief, *, attachments=None, session_key=None, tool_subset=None):
            if session_key == "task:t-1:stage:b" and fail_b_once["flag"]:
                call_count_per_stage["b"] += 1
                fail_b_once["flag"] = False
                raise DriverError("transient b fail")
            for k in call_count_per_stage:
                if session_key == f"task:t-1:stage:{k}":
                    call_count_per_stage[k] += 1
            return await super().chat(brief, attachments=attachments, session_key=session_key, tool_subset=tool_subset)

    driver = CountingDriver("x")
    runner = _make_runner({"x": driver}, tmp_path, retry_attempts=3)
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(stage_id="a", agent="x", brief_template="a", expected_artifact_type="t"),
            DAGNode(stage_id="b", agent="x", brief_template="b", inputs=["a"], expected_artifact_type="t"),
        ],
    )
    # First run: a succeeds, b fails once then succeeds on retry.
    await runner.run(dag)
    assert call_count_per_stage == {"a": 1, "b": 2}
    # Wait, actually b should be called twice (once fail, once succeed) before
    # the retry loop sees success — but the loop is per-stage; the retry
    # happens within the same call so b is called twice total. ✅

    # Now replay the whole task: a should be skipped (already completed),
    # b should NOT re-run (also already completed).
    call_count_per_stage["a"] = 0
    call_count_per_stage["b"] = 0
    fail_b_once["flag"] = False  # ensure no fail on this run
    await runner.run(dag)
    # Neither stage should be invoked — both completed.
    assert call_count_per_stage == {"a": 0, "b": 0}


@pytest.mark.asyncio
async def test_dag_runner_force_rerun(tmp_path: Path):
    driver = FakeDriver("x")
    runner = _make_runner({"x": driver}, tmp_path)
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(stage_id="a", agent="x", brief_template="a", expected_artifact_type="t"),
        ],
    )
    await runner.run(dag)
    driver.calls.clear()
    await runner.run(dag, force_rerun=True)
    assert len(driver.calls) == 1


@pytest.mark.asyncio
async def test_dag_runner_fail_fast_on_stage_error(tmp_path: Path):
    driver = FakeDriver("x")
    driver.fail_on.add("task:t-1:stage:a")
    runner = _make_runner({"x": driver}, tmp_path, retry_attempts=1)
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(stage_id="a", agent="x", brief_template="a", expected_artifact_type="t"),
        ],
    )
    with pytest.raises(StageExecutionError) as exc_info:
        await runner.run(dag)
    assert exc_info.value.stage_id == "a"


@pytest.mark.asyncio
async def test_dag_runner_retry_then_succeed(tmp_path: Path):
    """Driver fails once, then succeeds on retry."""
    call_count = {"n": 0}

    class RetryDriver(FakeDriver):
        async def chat(self, brief, *, attachments=None, session_key=None, tool_subset=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise DriverError("transient")
            return await super().chat(brief, attachments=attachments, session_key=session_key, tool_subset=tool_subset)

    driver = RetryDriver("x")
    runner = _make_runner({"x": driver}, tmp_path, retry_attempts=3)
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(stage_id="a", agent="x", brief_template="a", expected_artifact_type="t"),
        ],
    )
    results = await runner.run(dag)
    assert results["a"].content == "reply to: a"
    assert call_count["n"] == 2  # 1 fail + 1 success


@pytest.mark.asyncio
async def test_dag_runner_unknown_agent(tmp_path: Path):
    runner = _make_runner({"x": FakeDriver("x")}, tmp_path)
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(stage_id="a", agent="unknown-agent", brief_template="a", expected_artifact_type="t"),
        ],
    )
    with pytest.raises(StageExecutionError) as exc_info:
        await runner.run(dag)
    assert "unknown-agent" in str(exc_info.value.original)


@pytest.mark.asyncio
async def test_dag_runner_parallel_dispatch(tmp_path: Path):
    driver = FakeDriver("x")
    # Two parallel stages + one synth stage.
    dag = TaskDAG(
        task_id="t-1",
        nodes=[
            DAGNode(stage_id="a", agent="x", brief_template="a", expected_artifact_type="t"),
            DAGNode(stage_id="b1", agent="x", brief_template="b1", inputs=["a"], expected_artifact_type="t", parallel_group=1),
            DAGNode(stage_id="b2", agent="x", brief_template="b2", inputs=["a"], expected_artifact_type="t", parallel_group=1),
            DAGNode(stage_id="c", agent="x", brief_template="c", inputs=["b1", "b2"], expected_artifact_type="t"),
        ],
    )
    runner = _make_runner({"x": driver}, tmp_path)
    results = await runner.run(dag)
    assert set(results.keys()) == {"a", "b1", "b2", "c"}
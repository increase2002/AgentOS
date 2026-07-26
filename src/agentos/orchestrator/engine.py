"""Orchestrator Engine — main entry point for multi-agent task execution.

The Engine is the public API surface of AgentOS Plan B (ADR-0010). It
owns:

* a Driver registry (name -> ``BaseDriver`` instance)
* a CheckpointStore for partial-success persistence
* a TelemetryHook for evaluation (ADR-0004)
* a DAGRunner that walks the PlanDAG

Usage
-----

::

    engine = Engine(
        drivers={
            "openclaw": OpenClawDriver(...),
            "codex":    CodexAdapter(...),
            "claude":   AnthropicDriver(...),
            "gemini":   GeminiDriver(...),
        },
        concurrency_budget=4,
    )

    # Pre-built DAG:
    dag = TaskDAG(
        task_id="t-001",
        nodes=[
            DAGNode(stage_id="research", agent="openclaw", ...),
            DAGNode(stage_id="write",    agent="codex",    inputs=["research"], ...),
            DAGNode(stage_id="review",   agent="claude",   inputs=["write"], ...),
        ],
    )
    result = await engine.run(task_id="t-001", dag_payload=dag.model_dump())

    # Or: poll bus for TASK_REQUEST and dispatch automatically.
    from agentos.orchestrator.bus_loop import BusLoop
    loop = BusLoop(engine)
    await loop.run()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentos.drivers.base import BaseDriver
from agentos.orchestrator.checkpoint import (
    DEFAULT_CHECKPOINT_DIR,
    TaskCheckpoint,
    TaskCheckpointStore,
    TaskStatus,
)
from agentos.orchestrator.dag_runner import DAGRunner, StageResult
from agentos.schemas.dag import TaskDAG
from agentos.telemetry import JSONLHook, default_hook

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class OrchestratorError(Exception):
    """Raised when the Engine cannot execute a task."""


class UnknownAgentError(OrchestratorError):
    """The DAG references an agent name not in the Engine's driver registry."""


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass
class TaskResult:
    """Outcome of running a task end-to-end."""

    task_id: str
    status: TaskStatus
    stages: dict[str, StageResult] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    total_cost: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "stages": {
                sid: {
                    "stage_id": r.stage_id,
                    "agent": r.agent,
                    "content": r.content,
                    "artifact_ref": r.artifact_ref,
                    "cost": r.cost,
                    "elapsed_ms": r.elapsed_ms,
                }
                for sid, r in self.stages.items()
            },
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "total_cost": self.total_cost,
        }


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


class Engine:
    """Multi-agent task orchestrator."""

    def __init__(
        self,
        *,
        drivers: dict[str, BaseDriver],
        checkpoint_dir: Path | None = None,
        concurrency_budget: int = 4,
        retry_attempts: int = 3,
        telemetry: JSONLHook | None = None,
    ) -> None:
        """Construct an Engine.

        Parameters
        ----------
        drivers:
            Map of agent name -> driver instance. Names must match
            ``node.agent`` values in the DAG (e.g. ``"openclaw"``,
            ``"codex"``, ``"claude"``, ``"gemini"``).
        checkpoint_dir:
            Where per-task checkpoints live. Default
            ``G:/AgentOS/.agentos/checkpoints``.
        concurrency_budget:
            Per ADR-0006. Default 4.
        retry_attempts:
            Per ADR-0007. Default 3.
        telemetry:
            Optional ``JSONLHook``. Defaults to ``default_hook()``.
        """
        if not drivers:
            raise OrchestratorError("Engine requires at least one driver")
        self.drivers = drivers
        self.checkpoint_store = TaskCheckpointStore(
            base_dir=checkpoint_dir or DEFAULT_CHECKPOINT_DIR
        )
        self.telemetry = telemetry or default_hook()
        self.dag_runner = DAGRunner(
            resolve_driver=self._resolve_driver,
            checkpoint_store=self.checkpoint_store,
            telemetry=self.telemetry,
            concurrency_budget=concurrency_budget,
            retry_attempts=retry_attempts,
        )

    # ----------------------------------------------------------- registry

    def register_driver(self, name: str, driver: BaseDriver) -> None:
        """Add or replace a driver at runtime."""
        self.drivers[name] = driver

    def _resolve_driver(self, agent: str) -> BaseDriver:
        if agent not in self.drivers:
            raise UnknownAgentError(
                f"agent {agent!r} not in driver registry; "
                f"known={sorted(self.drivers.keys())}"
            )
        return self.drivers[agent]

    # --------------------------------------------------------------- run

    async def run(
        self,
        *,
        task_id: str,
        brief: str | None = None,
        dag_payload: dict | TaskDAG | None = None,
        force_rerun: bool = False,
    ) -> TaskResult:
        """Execute a task.

        Parameters
        ----------
        task_id:
            Unique task identifier. Reusing one triggers partial-success
            replay (skips completed stages unless ``force_rerun=True``).
        brief:
            Task brief (informational; passed to DAGRunner for templating).
        dag_payload:
            Pre-built DAG as dict (e.g. from Bus ``payload["dag"]``) or
            a ``TaskDAG`` instance. If ``None``, the Engine requires the
            caller to have already populated the DAG (Planner is not yet
            wired in MVP; future v0.2).
        force_rerun:
            Bypass checkpoint: re-run all stages even if completed.
        """
        dag = self._coerce_dag(task_id, dag_payload)
        if brief:
            logger.info("engine.run task=%s brief=%r", task_id, brief[:120])

        try:
            stage_results = await self.dag_runner.run(
                dag, force_rerun=force_rerun,
                brief_vars={"task_brief": brief} if brief else {},
            )
        except Exception as exc:
            # Failure already recorded in checkpoint + telemetry by DAGRunner.
            logger.error("task %s failed: %s", task_id, exc)
            cp = self.checkpoint_store.load(task_id)
            status = cp.status if cp else TaskStatus.FAILED
            return TaskResult(
                task_id=task_id,
                status=status,
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
            )

        # Aggregate cost.
        total_cost: dict[str, int] = {}
        for r in stage_results.values():
            for k, v in r.cost.items():
                total_cost[k] = total_cost.get(k, 0) + v

        cp = self.checkpoint_store.load(task_id)
        return TaskResult(
            task_id=task_id,
            status=cp.status if cp else TaskStatus.COMPLETED,
            stages=stage_results,
            completed_at=datetime.now(timezone.utc),
            total_cost=total_cost,
        )

    # ---------------------------------------------------------- helpers

    def _coerce_dag(
        self, task_id: str, payload: dict | TaskDAG | None
    ) -> TaskDAG:
        if payload is None:
            raise OrchestratorError(
                f"task {task_id!r}: no DAG supplied; "
                f"Planner is not yet wired (v0.2); "
                f"pass dag_payload with a TaskDAG dict."
            )
        if isinstance(payload, TaskDAG):
            dag = payload
        elif isinstance(payload, dict):
            dag = TaskDAG.model_validate(payload)
        else:
            raise OrchestratorError(
                f"task {task_id!r}: dag_payload must be dict or TaskDAG; "
                f"got {type(payload).__name__}"
            )
        # Fail-fast: every agent referenced in the DAG must be in the
        # driver registry. Surfaces configuration errors before we start
        # executing (otherwise UnknownAgentError would be wrapped as a
        # StageExecutionError and only surface after first stage fail).
        missing = sorted({
            node.agent for node in dag.nodes
            if node.agent not in self.drivers
        })
        if missing:
            raise UnknownAgentError(
                f"task {task_id!r}: agents not in driver registry: {missing}; "
                f"known={sorted(self.drivers.keys())}"
            )
        return dag

    # ----------------------------------------------------------- inspect

    def get_checkpoint(self, task_id: str) -> TaskCheckpoint | None:
        return self.checkpoint_store.load(task_id)

    def list_tasks(self) -> list[str]:
        return self.checkpoint_store.list_tasks()


__all__ = [
    "Engine",
    "OrchestratorError",
    "UnknownAgentError",
    "TaskResult",
]
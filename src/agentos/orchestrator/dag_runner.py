"""DAG Runner — walks a TaskDAG, dispatches stages to drivers.

Execution model
---------------

1. **Topological order** — ``plan_waves(dag)`` partitions nodes into waves
   where every node in wave N only depends on nodes in waves 0..N-1.
2. **Within a wave** — nodes with the same non-``None`` ``parallel_group``
   are launched concurrently via ``asyncio.gather``. Nodes with
   ``parallel_group=None`` (or different values) run sequentially within
   the wave (but still in parallel with subsequent waves if no deps).
3. **Concurrency budget** — single ``asyncio.Semaphore`` wraps every
   driver call (per ADR-0006). Streaming holds the slot for the whole
   round (not per chunk) — for v0.1 we treat every call as non-streaming.
4. **Checkpoint + telemetry** — before/after each stage: ``STAGE_START``/
   ``STAGE_END`` telemetry events, plus checkpoint state transitions.
5. **Partial success** — completed stages are skipped on re-run (unless
   ``force_rerun=True``). Failed stages propagate the error to caller;
   the Engine decides retry vs fail-fast.

Caveats
-------

* Drivers are referenced by ``node.agent`` (a name in ``AGENT_CHOICES``).
  The Engine resolves names to driver instances via ``DriverRegistry``.
* Driver calls are awaited (``await driver.chat(...)``) per the v0.1
  vendor-wrapper interface.
* This module knows nothing about the Bus; bus integration lives in
  ``bus_loop.py``.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from agentos.orchestrator.checkpoint import (
    StageState,
    StageStatus,
    TaskCheckpoint,
    TaskCheckpointStore,
)
from agentos.orchestrator.session_keys import build_stage_key, build_subtask_key
from agentos.schemas.dag import DAGNode, TaskDAG
from agentos.telemetry import JSONLHook, TelemetryEventType

if TYPE_CHECKING:  # pragma: no cover
    from agentos.drivers.base import BaseDriver

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class StageExecutionError(Exception):
    """Raised when a stage fails after all retries (fail-fast per ADR-0007)."""

    def __init__(self, stage_id: str, agent: str, original: Exception) -> None:
        super().__init__(
            f"stage {stage_id!r} (agent={agent!r}) failed: "
            f"{type(original).__name__}: {original}"
        )
        self.stage_id = stage_id
        self.agent = agent
        self.original = original


# --------------------------------------------------------------------------- #
# Wave plan
# --------------------------------------------------------------------------- #


@dataclass
class Wave:
    """One execution wave in the DAG.

    All nodes in ``Wave.nodes`` can run concurrently (subject to the
    Concurrency Budget semaphore). Within a wave, nodes sharing a
    ``parallel_group`` are gathered together; nodes in different (or None)
    groups still run in the same wave but are scheduled sequentially
    relative to each other (the orchestrator processes groups in order).
    """

    index: int
    nodes: list[DAGNode]


def plan_waves(dag: TaskDAG) -> list[Wave]:
    """Compute wave plan from a DAG.

    Uses Kahn-style topological sort with parallel_group merging:
    nodes with no pending inputs go into the same wave as long as their
    parallel_group matches the current wave's group (or is None).

    Raises ``ValueError`` on cyclic DAGs.
    """
    incoming: dict[str, set[str]] = {n.stage_id: set(n.inputs) for n in dag.nodes}
    nodes_by_id: dict[str, DAGNode] = {n.stage_id: n for n in dag.nodes}

    waves: list[Wave] = []
    remaining = set(incoming.keys())

    while remaining:
        # Nodes whose inputs are all satisfied (not in remaining).
        ready_ids = sorted(
            sid for sid in remaining if not (incoming[sid] & remaining)
        )
        if not ready_ids:
            raise ValueError(
                f"cyclic DAG detected; remaining={sorted(remaining)}"
            )

        # Group ready nodes by parallel_group. None means "sequential".
        groups: dict[Any, list[str]] = defaultdict(list)
        for sid in ready_ids:
            node = nodes_by_id[sid]
            groups[node.parallel_group].append(sid)

        wave_nodes: list[DAGNode] = []
        # Process groups in deterministic order: None first, then ints.
        sorted_groups = sorted(
            groups.keys(),
            key=lambda g: (g is None, g if isinstance(g, int) else -1),
        )
        for g in sorted_groups:
            for sid in groups[g]:
                wave_nodes.append(nodes_by_id[sid])
                remaining.discard(sid)

        waves.append(Wave(index=len(waves), nodes=wave_nodes))

    return waves


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

DriverResolver = Callable[[str], "BaseDriver"]
BriefRenderer = Callable[[str, dict[str, Any]], str]


@dataclass
class StageResult:
    """Outcome of running one stage."""

    stage_id: str
    agent: str
    content: str
    artifact_ref: str | None
    cost: dict[str, int]
    elapsed_ms: int


class DAGRunner:
    """Walk a TaskDAG, dispatching stages to drivers with concurrency control."""

    def __init__(
        self,
        *,
        resolve_driver: DriverResolver,
        checkpoint_store: TaskCheckpointStore,
        telemetry: JSONLHook,
        concurrency_budget: int = 4,
        retry_attempts: int = 3,
        retry_base_delay_s: float = 1.0,
    ) -> None:
        self.resolve_driver = resolve_driver
        self.checkpoint_store = checkpoint_store
        self.telemetry = telemetry
        self.semaphore = asyncio.Semaphore(concurrency_budget)
        self.retry_attempts = retry_attempts
        self.retry_base_delay_s = retry_base_delay_s

    # ----------------------------------------------------------------- run

    async def run(
        self,
        dag: TaskDAG,
        *,
        force_rerun: bool = False,
        brief_vars: dict[str, Any] | None = None,
    ) -> dict[str, StageResult]:
        """Execute all waves. Return map of stage_id -> result.

        Parallel stages within a wave share a single ``TaskCheckpoint``
        object so concurrent saves do not clobber each other's stage
        updates. This is critical for partial-success replay correctness.
        """
        brief_vars = dict(brief_vars or {})
        results: dict[str, StageResult] = {}

        # Single shared cp across all stages in this task — ensures save()
        # from parallel branches merges into one consistent view.
        shared_cp = self.checkpoint_store.ensure(
            dag.task_id, dag_cache_key=dag.dag_cache_key,
        )

        for wave in plan_waves(dag):
            logger.info(
                "wave %d: %d stage(s) [%s]",
                wave.index, len(wave.nodes),
                ", ".join(n.stage_id for n in wave.nodes),
            )
            # Group within wave by parallel_group; sequential groups first.
            within_wave_groups: dict[Any, list[DAGNode]] = defaultdict(list)
            for node in wave.nodes:
                within_wave_groups[node.parallel_group].append(node)

            sorted_group_keys = sorted(
                within_wave_groups.keys(),
                key=lambda g: (g is None, g if isinstance(g, int) else -1),
            )

            for g in sorted_group_keys:
                group_nodes = within_wave_groups[g]
                if len(group_nodes) == 1:
                    result = await self._run_one(
                        group_nodes[0], dag, brief_vars, force_rerun,
                        results, shared_cp,
                    )
                    results[group_nodes[0].stage_id] = result
                else:
                    # Parallel dispatch — gather, but each coroutine still
                    # acquires the semaphore internally.
                    coros = [
                        self._run_one(node, dag, brief_vars, force_rerun, results, shared_cp)
                        for node in group_nodes
                    ]
                    gathered = await asyncio.gather(*coros, return_exceptions=True)
                    for node, outcome in zip(group_nodes, gathered):
                        if isinstance(outcome, StageExecutionError):
                            # Fail-fast: re-raise immediately (caller can
                            # catch and decide to retry whole task or
                            # checkpoint partial).
                            raise outcome
                        if isinstance(outcome, BaseException):
                            raise outcome
                        results[node.stage_id] = outcome

        return results

    # -------------------------------------------------------------- one stage

    async def _run_one(
        self,
        node: DAGNode,
        dag: TaskDAG,
        brief_vars: dict[str, Any],
        force_rerun: bool,
        prior_results: dict[str, StageResult],
        shared_cp: TaskCheckpoint | None = None,
    ) -> StageResult:
        """Run one stage with retry + telemetry + checkpoint.

        ``shared_cp`` is the per-task checkpoint shared across all stages
        in the same ``run()`` call. Without sharing, parallel stages'
        independent ``ensure()`` calls produce different ``cp`` objects
        whose ``save()`` calls clobber each other's stage updates.
        """
        # Skip if already completed (partial-success replay).
        cp = shared_cp or self.checkpoint_store.ensure(
            dag.task_id, dag_cache_key=dag.dag_cache_key,
        )
        prior = cp.stages.get(node.stage_id)
        if not force_rerun and prior and prior.status == StageStatus.COMPLETED:
            logger.info(
                "stage %s already completed (checkpoint hit); skipping",
                node.stage_id,
            )
            # Reconstruct a StageResult from checkpoint (cost-aware).
            return StageResult(
                stage_id=node.stage_id,
                agent=node.agent,
                content=prior.result_preview,
                artifact_ref=prior.result_artifact,
                cost=prior.cost,
                elapsed_ms=0,
            )

        # Resolve driver.
        driver = self._resolve_driver_safe(node)

        # Build session key.
        session_key = build_stage_key(dag.task_id, node.stage_id)

        # Render brief from template + upstream artifacts.
        upstream_vars = dict(brief_vars)
        for sid in node.inputs:
            if sid in prior_results:
                # stage_id may contain hyphens (e.g. "research-web"); convert
                # to underscore so Python format() accepts it as a kwarg.
                upstream_vars[sid.replace("-", "_")] = prior_results[sid].content
        brief = node.brief_template.format(**upstream_vars)

        # Telemetry: STAGE_START.
        self.telemetry.record(
            TelemetryEventType.STAGE_START,
            session_key=session_key,
            driver=type(driver).__name__,
            payload={
                "stage_id": node.stage_id,
                "agent": node.agent,
                "tool_subset": node.tool_subset,
                "expected_artifact_type": node.expected_artifact_type,
            },
        )

        # Checkpoint: PENDING -> RUNNING.
        cp.stages[node.stage_id] = StageState(
            status=StageStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        self.checkpoint_store.save(cp)

        # Retry loop.
        last_exc: Exception | None = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                async with self.semaphore:
                    start = datetime.now(timezone.utc)
                    self.telemetry.record(
                        TelemetryEventType.DRIVER_CHAT_IN,
                        session_key=session_key,
                        driver=type(driver).__name__,
                        payload={"stage_id": node.stage_id, "brief": brief[:500]},
                    )
                    result = await driver.chat(
                        brief,
                        attachments=None,
                        session_key=session_key,
                        tool_subset=node.tool_subset,
                    )
                    elapsed_ms = int(
                        (datetime.now(timezone.utc) - start).total_seconds() * 1000
                    )
                    self.telemetry.record(
                        TelemetryEventType.DRIVER_CHAT_OUT,
                        session_key=session_key,
                        driver=type(driver).__name__,
                        payload={
                            "stage_id": node.stage_id,
                            "result_preview": (getattr(result, "content", "") or "")[:200],
                        },
                        metadata={
                            "latency_ms": elapsed_ms,
                            "token_usage": getattr(result, "usage", None) or {},
                        },
                    )

                # Success: write checkpoint + return.
                stage_result = StageResult(
                    stage_id=node.stage_id,
                    agent=node.agent,
                    content=getattr(result, "content", "") or "",
                    artifact_ref=(
                        (getattr(result, "artifact", None) or {}).get("path")
                        if getattr(result, "artifact", None)
                        else None
                    ),
                    cost=dict(getattr(result, "usage", None) or {}),
                    elapsed_ms=elapsed_ms,
                )
                cp.stages[node.stage_id] = StageState(
                    status=StageStatus.COMPLETED,
                    result_artifact=stage_result.artifact_ref,
                    result_preview=stage_result.content[:500],
                    retries=attempt - 1,
                    started_at=cp.stages[node.stage_id].started_at,
                    completed_at=datetime.now(timezone.utc),
                    cost=stage_result.cost,
                )
                # Roll up task status: completed if all done.
                all_done = all(
                    s.status in (StageStatus.COMPLETED, StageStatus.SKIPPED)
                    for s in cp.stages.values()
                )
                if all_done:
                    from agentos.orchestrator.checkpoint import TaskStatus
                    cp.status = TaskStatus.COMPLETED
                self.checkpoint_store.save(cp)

                self.telemetry.record(
                    TelemetryEventType.STAGE_END,
                    session_key=session_key,
                    driver=type(driver).__name__,
                    payload={"stage_id": node.stage_id, "status": "completed"},
                    metadata={"elapsed_ms": elapsed_ms, "attempts": attempt},
                )

                return stage_result

            except Exception as exc:
                last_exc = exc
                self.telemetry.record(
                    TelemetryEventType.ERROR,
                    session_key=session_key,
                    driver=type(driver).__name__,
                    payload={"stage_id": node.stage_id, "error": type(exc).__name__},
                    metadata={
                        "error_msg": str(exc)[:500],
                        "attempt": attempt,
                    },
                )
                if attempt < self.retry_attempts:
                    import random
                    delay = self.retry_base_delay_s * (2 ** (attempt - 1))
                    delay += random.uniform(0, 0.5)  # jitter
                    logger.warning(
                        "stage %s attempt %d/%d failed (%s); retrying in %.2fs",
                        node.stage_id, attempt, self.retry_attempts,
                        type(exc).__name__, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "stage %s failed after %d attempts: %s",
                        node.stage_id, attempt, exc,
                    )

        # All retries exhausted.
        cp.stages[node.stage_id] = StageState(
            status=StageStatus.FAILED,
            retries=self.retry_attempts,
            started_at=cp.stages[node.stage_id].started_at,
            completed_at=datetime.now(timezone.utc),
            error=f"{type(last_exc).__name__}: {last_exc}"[:500] if last_exc else "unknown",
        )
        from agentos.orchestrator.checkpoint import TaskStatus
        cp.status = TaskStatus.FAILED
        self.checkpoint_store.save(cp)

        self.telemetry.record(
            TelemetryEventType.STAGE_END,
            session_key=session_key,
            driver=type(driver).__name__,
            payload={"stage_id": node.stage_id, "status": "failed"},
        )
        raise StageExecutionError(
            stage_id=node.stage_id,
            agent=node.agent,
            original=last_exc or RuntimeError("unknown error"),
        )

    # -------------------------------------------------------------- helpers

    def _resolve_driver_safe(self, node: DAGNode) -> "BaseDriver":
        try:
            return self.resolve_driver(node.agent)
        except KeyError as exc:
            raise StageExecutionError(
                stage_id=node.stage_id,
                agent=node.agent,
                original=exc,
            )


__all__ = [
    "StageResult",
    "StageExecutionError",
    "Wave",
    "plan_waves",
    "DAGRunner",
]
"""Orchestrator Engine — multi-agent task execution (Plan B per ADR-0010).

Modules
-------

* ``engine`` — :class:`Engine`, the public API.
* ``dag_runner`` — :class:`DAGRunner`, walks a TaskDAG with concurrency
  budget + retry + checkpoint + telemetry.
* ``checkpoint`` — :class:`TaskCheckpointStore`, partial-success persistence.
* ``session_keys`` — build / validate ``task:<id>:stage:<id>[:sub:<id>]``.
* ``bus_loop`` — :class:`BusLoop`, polls ``bus.jsonl`` and dispatches
  ``TASK_REQUEST`` messages to the Engine.
"""

from agentos.orchestrator.bus_loop import ORCHESTRATOR_AGENT_NAME, BusLoop
from agentos.orchestrator.checkpoint import (
    DEFAULT_CHECKPOINT_DIR,
    StageState,
    StageStatus,
    TaskCheckpoint,
    TaskCheckpointStore,
    TaskStatus,
)
from agentos.orchestrator.dag_runner import (
    DAGRunner,
    StageExecutionError,
    StageResult,
    Wave,
    plan_waves,
)
from agentos.orchestrator.engine import (
    Engine,
    OrchestratorError,
    TaskResult,
    UnknownAgentError,
)
from agentos.orchestrator.session_keys import (
    InvalidSessionKeyError,
    build_stage_key,
    build_subtask_key,
    parse_session_key,
    validate_session_key,
)

__all__ = [
    # engine
    "Engine",
    "TaskResult",
    "OrchestratorError",
    "UnknownAgentError",
    # dag_runner
    "DAGRunner",
    "StageResult",
    "StageExecutionError",
    "Wave",
    "plan_waves",
    # checkpoint
    "TaskCheckpointStore",
    "TaskCheckpoint",
    "StageState",
    "StageStatus",
    "TaskStatus",
    "DEFAULT_CHECKPOINT_DIR",
    # session_keys
    "build_stage_key",
    "build_subtask_key",
    "parse_session_key",
    "validate_session_key",
    "InvalidSessionKeyError",
    # bus_loop
    "BusLoop",
    "ORCHESTRATOR_AGENT_NAME",
]
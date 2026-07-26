"""Task Checkpoint Store — partial-success persistence.

Each task gets one ``{task_id}.json`` file holding per-stage status,
results, retry counts, and accumulated cost. Re-running ``Engine.run()``
loads the file and skips already-completed stages.

Format
------

``G:/AgentOS/.agentos/checkpoints/{task_id}.json``::

    {
      "task_id": "t-001",
      "dag_cache_key": null,
      "created_at": "2026-07-26T13:00:00+00:00",
      "updated_at": "2026-07-26T13:05:00+00:00",
      "status": "running" | "completed" | "failed",
      "stages": {
        "research": {
          "status": "completed",
          "result_artifact": "G:/AgentOS/artifacts/t-001/research/result.json",
          "retries": 0,
          "started_at": "2026-07-26T13:01:00+00:00",
          "completed_at": "2026-07-26T13:02:00+00:00",
          "cost": {"prompt_tokens": 100, "completion_tokens": 50}
        },
        "write": { "status": "pending", ... }
      }
    }

GC: checkpoints older than 30 days are removed (matches ADR-0008 artifact GC).

Thread safety
-------------

Single process; concurrent writes from multiple ``Engine`` instances are
NOT supported (would need file lock or sqlite). For MVP we assume one
Engine per host.

Crash safety
------------

Writes go through a temp file + ``os.replace`` so a crash mid-write
leaves the previous version intact.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CHECKPOINT_DIR = Path("G:/AgentOS/.agentos/checkpoints")

CHECKPOINT_TTL_DAYS = 30  # matches ADR-0008 artifact GC


class StageStatus(str, Enum):
    """Per-stage lifecycle states."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"  # inputs missing or DAG cache hit


class TaskStatus(str, Enum):
    """Overall task lifecycle states."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StageState:
    """Per-stage checkpoint data."""

    status: StageStatus = StageStatus.PENDING
    result_artifact: str | None = None
    result_preview: str = ""  # small text snippet for debugging
    retries: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cost: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        # Datetimes -> ISO strings.
        if self.started_at is not None:
            d["started_at"] = self.started_at.isoformat()
        if self.completed_at is not None:
            d["completed_at"] = self.completed_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StageState":
        d = dict(d)
        if "status" in d:
            d["status"] = StageStatus(d["status"])
        for k in ("started_at", "completed_at"):
            if d.get(k) is not None:
                d[k] = datetime.fromisoformat(d[k])
        # Drop unknown keys (forward compat).
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class TaskCheckpoint:
    """Per-task checkpoint aggregate."""

    task_id: str
    dag_cache_key: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: TaskStatus = TaskStatus.RUNNING
    stages: dict[str, StageState] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "dag_cache_key": self.dag_cache_key,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "status": self.status.value,
            "stages": {sid: s.to_dict() for sid, s in self.stages.items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskCheckpoint":
        return cls(
            task_id=d["task_id"],
            dag_cache_key=d.get("dag_cache_key"),
            created_at=datetime.fromisoformat(d["created_at"]),
            updated_at=datetime.fromisoformat(d["updated_at"]),
            status=TaskStatus(d.get("status", TaskStatus.RUNNING.value)),
            stages={
                sid: StageState.from_dict(s)
                for sid, s in d.get("stages", {}).items()
            },
        )


class TaskCheckpointStore:
    """Filesystem-backed checkpoint store (one JSON file per task)."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else DEFAULT_CHECKPOINT_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- paths

    def _path(self, task_id: str) -> Path:
        # Sanitize: same rules as session_keys._check_id.
        import re
        if not re.match(r"^[A-Za-z0-9_-]+$", task_id):
            raise ValueError(f"invalid task_id: {task_id!r}")
        return self.base_dir / f"{task_id}.json"

    # ----------------------------------------------------------------- API

    def load(self, task_id: str) -> TaskCheckpoint | None:
        """Load an existing checkpoint, or ``None`` if none exists."""
        path = self._path(task_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return TaskCheckpoint.from_dict(data)
        except Exception:
            logger.exception("checkpoint corrupted for task %s; ignoring", task_id)
            return None

    def save(self, checkpoint: TaskCheckpoint) -> None:
        """Atomically write the checkpoint (temp file + replace)."""
        checkpoint.updated_at = datetime.now(timezone.utc)
        path = self._path(checkpoint.task_id)
        data = json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2)
        with self._lock:
            # Crash-safe: write to temp file in same dir, then atomic rename.
            fd, tmp = tempfile.mkstemp(
                prefix=f".{checkpoint.task_id}.",
                suffix=".tmp",
                dir=str(self.base_dir),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(data)
                os.replace(tmp, path)
            except Exception:
                # Clean up tmp on failure.
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

    def ensure(self, task_id: str, *, dag_cache_key: str | None = None) -> TaskCheckpoint:
        """Load existing checkpoint or create a new one (and persist it).

        The new checkpoint is written to disk so subsequent ``list_tasks()``
        and ``gc()`` calls see it.
        """
        existing = self.load(task_id)
        if existing is not None:
            return existing
        cp = TaskCheckpoint(task_id=task_id, dag_cache_key=dag_cache_key)
        self.save(cp)
        return cp

    def update_stage(
        self,
        task_id: str,
        stage_id: str,
        **changes: Any,
    ) -> TaskCheckpoint:
        """Load-modify-save: update a stage's fields."""
        cp = self.ensure(task_id)
        stage = cp.stages.get(stage_id) or StageState()
        for k, v in changes.items():
            setattr(stage, k, v)
        cp.stages[stage_id] = stage
        self.save(cp)
        return cp

    # ----------------------------------------------------------------- GC

    def gc(self, ttl_days: int = CHECKPOINT_TTL_DAYS) -> int:
        """Remove checkpoints older than ``ttl_days`` (based on created_at).

        Returns count removed.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
        removed = 0
        for path in self.base_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                ts = datetime.fromisoformat(data["created_at"])
                if ts < cutoff:
                    path.unlink()
                    removed += 1
            except Exception:
                logger.warning("skipping malformed checkpoint %s", path)
        return removed

    # ----------------------------------------------------------------- bulk

    def list_tasks(self) -> list[str]:
        """List all task_ids with a checkpoint file."""
        return [p.stem for p in self.base_dir.glob("*.json")]


__all__ = [
    "DEFAULT_CHECKPOINT_DIR",
    "CHECKPOINT_TTL_DAYS",
    "StageStatus",
    "TaskStatus",
    "StageState",
    "TaskCheckpoint",
    "TaskCheckpointStore",
]
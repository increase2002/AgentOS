"""Tests for TaskCheckpointStore (partial-success persistence)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentos.orchestrator.checkpoint import (
    CHECKPOINT_TTL_DAYS,
    StageState,
    StageStatus,
    TaskCheckpoint,
    TaskCheckpointStore,
    TaskStatus,
)


@pytest.fixture
def store(tmp_path: Path) -> TaskCheckpointStore:
    return TaskCheckpointStore(base_dir=tmp_path)


def test_store_returns_none_when_missing(store: TaskCheckpointStore):
    assert store.load("nope") is None


def test_ensure_creates_new(store: TaskCheckpointStore):
    cp = store.ensure("t-001")
    assert cp.task_id == "t-001"
    assert cp.status == TaskStatus.RUNNING
    assert cp.stages == {}


def test_save_and_load_round_trip(store: TaskCheckpointStore):
    cp = store.ensure("t-001", dag_cache_key="abc")
    cp.stages["research"] = StageState(
        status=StageStatus.COMPLETED,
        result_artifact="G:/AgentOS/artifacts/t-001/research/result.json",
        result_preview="snippet",
        cost={"prompt_tokens": 100, "completion_tokens": 50},
    )
    store.save(cp)

    loaded = store.load("t-001")
    assert loaded is not None
    assert loaded.task_id == "t-001"
    assert loaded.dag_cache_key == "abc"
    assert "research" in loaded.stages
    assert loaded.stages["research"].status == StageStatus.COMPLETED
    assert loaded.stages["research"].cost == {"prompt_tokens": 100, "completion_tokens": 50}


def test_update_stage_persists(store: TaskCheckpointStore):
    store.update_stage("t-001", "research", status=StageStatus.RUNNING)
    cp = store.load("t-001")
    assert cp.stages["research"].status == StageStatus.RUNNING

    store.update_stage("t-001", "research", status=StageStatus.COMPLETED)
    cp = store.load("t-001")
    assert cp.stages["research"].status == StageStatus.COMPLETED


def test_invalid_task_id_rejected(store: TaskCheckpointStore):
    with pytest.raises(ValueError):
        store._path("bad/id")
    with pytest.raises(ValueError):
        store._path("bad id")


def test_gc_removes_old_checkpoints(store: TaskCheckpointStore, tmp_path: Path):
    cp = store.ensure("t-old")
    cp.created_at = datetime.now(timezone.utc) - timedelta(days=CHECKPOINT_TTL_DAYS + 1)
    cp.updated_at = cp.created_at
    store.save(cp)

    cp2 = store.ensure("t-new")
    store.save(cp2)

    removed = store.gc()
    assert removed == 1
    assert store.load("t-old") is None
    assert store.load("t-new") is not None


def test_list_tasks(store: TaskCheckpointStore):
    store.ensure("t-a")
    store.ensure("t-b")
    store.ensure("t-c")
    tasks = sorted(store.list_tasks())
    assert tasks == ["t-a", "t-b", "t-c"]


def test_corrupted_checkpoint_returns_none(store: TaskCheckpointStore):
    # Write a corrupted file directly.
    (store.base_dir / "t-broken.json").write_text("{not valid json", encoding="utf-8")
    assert store.load("t-broken") is None


def test_stage_state_round_trip_with_datetimes():
    s = StageState(
        status=StageStatus.RUNNING,
        started_at=datetime(2026, 7, 26, 13, 0, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 7, 26, 13, 5, 0, tzinfo=timezone.utc),
    )
    d = s.to_dict()
    assert isinstance(d["started_at"], str)
    s2 = StageState.from_dict(d)
    assert s2.started_at == s.started_at
    assert s2.status == StageStatus.RUNNING


def test_stage_state_from_dict_drops_unknown_keys():
    s = StageState.from_dict({
        "status": "completed",
        "unknown_field": "should be dropped",
        "retries": 2,
    })
    assert s.status == StageStatus.COMPLETED
    assert s.retries == 2


def test_atomic_write_no_partial_files(store: TaskCheckpointStore):
    """Crash-safe: ensure no .tmp files leak after save."""
    cp = store.ensure("t-001")
    cp.stages["s"] = StageState(status=StageStatus.COMPLETED)
    store.save(cp)
    leftover = list(store.base_dir.glob("*.tmp"))
    assert leftover == []
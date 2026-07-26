"""Tests for TelemetryConsumer (per ADR-0004 eval loop)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentos.telemetry import JSONLHook, TelemetryConsumer, TelemetryEventType
from agentos.telemetry.jsonl import TelemetryEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_event(
    path: Path,
    *,
    event_type: TelemetryEventType,
    timestamp: datetime,
    driver: str | None = None,
    session_key: str | None = None,
    metadata: dict | None = None,
    payload: dict | None = None,
) -> None:
    """Append one event to a telemetry file (mimicking JSONLHook output)."""
    e = TelemetryEvent(
        event_type=event_type,
        timestamp=timestamp,
        driver=driver,
        session_key=session_key,
        metadata=metadata or {},
        payload=payload or {},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(e.model_dump_json() + "\n")


# ---------------------------------------------------------------------------
# list_dates
# ---------------------------------------------------------------------------


def test_list_dates_empty(tmp_path: Path) -> None:
    c = TelemetryConsumer(tmp_path)
    assert c.list_dates() == []


def test_list_dates_returns_sorted_dates(tmp_path: Path) -> None:
    from datetime import date
    (tmp_path / "2026-07-26.jsonl").write_text("")
    (tmp_path / "2026-07-25.jsonl").write_text("")
    (tmp_path / "2026-07-27.jsonl").write_text("")
    (tmp_path / "ignore-me.txt").write_text("")

    c = TelemetryConsumer(tmp_path)
    assert c.list_dates() == [date(2026, 7, 25), date(2026, 7, 26), date(2026, 7, 27)]


# ---------------------------------------------------------------------------
# iter_events
# ---------------------------------------------------------------------------


def test_iter_events_for_specific_date(tmp_path: Path) -> None:
    d1 = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
    d2 = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
    _write_event(tmp_path / "2026-07-26.jsonl", event_type=TelemetryEventType.STAGE_START, timestamp=d1)
    _write_event(tmp_path / "2026-07-27.jsonl", event_type=TelemetryEventType.STAGE_START, timestamp=d2)

    from datetime import date
    c = TelemetryConsumer(tmp_path)
    events = list(c.iter_events(date=date(2026, 7, 26)))
    assert len(events) == 1
    assert events[0].timestamp.date().isoformat() == "2026-07-26"


def test_iter_events_filter_by_driver(tmp_path: Path) -> None:
    ts = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
    _write_event(tmp_path / "2026-07-26.jsonl",
                event_type=TelemetryEventType.DRIVER_CHAT_OUT,
                timestamp=ts, driver="OpenClawDriver")
    _write_event(tmp_path / "2026-07-26.jsonl",
                event_type=TelemetryEventType.DRIVER_CHAT_OUT,
                timestamp=ts, driver="CodexAdapter")

    c = TelemetryConsumer(tmp_path)
    events = list(c.iter_events(driver="OpenClawDriver"))
    assert len(events) == 1
    assert events[0].driver == "OpenClawDriver"


def test_iter_events_filter_by_event_type(tmp_path: Path) -> None:
    ts = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
    _write_event(tmp_path / "2026-07-26.jsonl",
                event_type=TelemetryEventType.DRIVER_CHAT_OUT, timestamp=ts)
    _write_event(tmp_path / "2026-07-26.jsonl",
                event_type=TelemetryEventType.STAGE_START, timestamp=ts)
    _write_event(tmp_path / "2026-07-26.jsonl",
                event_type=TelemetryEventType.ERROR, timestamp=ts)

    c = TelemetryConsumer(tmp_path)
    errors = list(c.iter_events(event_type=TelemetryEventType.ERROR))
    assert len(errors) == 1


def test_iter_events_filter_by_session_key(tmp_path: Path) -> None:
    ts = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
    _write_event(tmp_path / "2026-07-26.jsonl",
                event_type=TelemetryEventType.STAGE_START,
                timestamp=ts, session_key="task:t-001:stage:research")
    _write_event(tmp_path / "2026-07-26.jsonl",
                event_type=TelemetryEventType.STAGE_START,
                timestamp=ts, session_key="task:t-002:stage:research")

    c = TelemetryConsumer(tmp_path)
    events = list(c.iter_events(session_key="task:t-001:stage:research"))
    assert len(events) == 1


def test_iter_events_filter_by_time_range(tmp_path: Path) -> None:
    from datetime import date
    ts1 = datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
    ts3 = datetime(2026, 7, 26, 11, 0, tzinfo=timezone.utc)
    for ts in [ts1, ts2, ts3]:
        _write_event(tmp_path / "2026-07-26.jsonl",
                    event_type=TelemetryEventType.STAGE_START, timestamp=ts)

    c = TelemetryConsumer(tmp_path)
    events = list(c.iter_events(date=date(2026, 7, 26),
                                 since=datetime(2026, 7, 26, 9, 30, tzinfo=timezone.utc),
                                 until=datetime(2026, 7, 26, 10, 30, tzinfo=timezone.utc)))
    assert len(events) == 1
    assert events[0].timestamp == ts2


def test_iter_events_skips_corrupt_lines(tmp_path: Path) -> None:
    from datetime import date
    path = tmp_path / "2026-07-26.jsonl"
    ts = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
    _write_event(path, event_type=TelemetryEventType.STAGE_START, timestamp=ts)
    with path.open("a", encoding="utf-8") as f:
        f.write("garbage line\n")
        f.write(json.dumps({"not": "a valid event"}) + "\n")
    _write_event(path, event_type=TelemetryEventType.STAGE_END, timestamp=ts)

    c = TelemetryConsumer(tmp_path)
    events = list(c.iter_events(date=date(2026, 7, 26)))
    assert len(events) == 2  # only the valid ones


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


def test_summary_aggregates_by_driver_and_event_type(tmp_path: Path) -> None:
    from datetime import date
    ts = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
    path = tmp_path / "2026-07-26.jsonl"
    _write_event(path, event_type=TelemetryEventType.DRIVER_CHAT_IN,
                timestamp=ts, driver="A")
    _write_event(path, event_type=TelemetryEventType.DRIVER_CHAT_OUT,
                timestamp=ts, driver="A")
    _write_event(path, event_type=TelemetryEventType.DRIVER_CHAT_OUT,
                timestamp=ts, driver="B")
    _write_event(path, event_type=TelemetryEventType.ERROR,
                timestamp=ts)

    c = TelemetryConsumer(tmp_path)
    s = c.summary(date=date(2026, 7, 26))
    assert s["total_events"] == 4
    assert s["by_driver"] == {"A": 2, "B": 1, "(none)": 1}
    assert s["by_event_type"]["error"] == 1
    assert s["errors"] == 1


# ---------------------------------------------------------------------------
# cost_estimate
# ---------------------------------------------------------------------------


def test_cost_estimate_aggregates_token_usage(tmp_path: Path) -> None:
    from datetime import date
    ts = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
    path = tmp_path / "2026-07-26.jsonl"
    # 2 events, same driver, total in=300, out=150
    _write_event(path, event_type=TelemetryEventType.DRIVER_CHAT_OUT,
                timestamp=ts, driver="A",
                metadata={"token_usage": {"in": 100, "out": 50}})
    _write_event(path, event_type=TelemetryEventType.DRIVER_CHAT_OUT,
                timestamp=ts, driver="A",
                metadata={"token_usage": {"in": 200, "out": 100}})
    # Different driver
    _write_event(path, event_type=TelemetryEventType.DRIVER_CHAT_OUT,
                timestamp=ts, driver="B",
                metadata={"token_usage": {"in": 50, "out": 25}})

    c = TelemetryConsumer(tmp_path)
    cost = c.cost_estimate(date=date(2026, 7, 26))
    assert cost["total_input_tokens"] == 350
    assert cost["total_output_tokens"] == 175
    assert cost["by_driver"]["A"] == {"in": 300, "out": 150}
    assert cost["by_driver"]["B"] == {"in": 50, "out": 25}
    # 350/1000*0.00015 + 175/1000*0.0006 = 0.0000525 + 0.000105 = 0.0001575
    assert abs(cost["estimated_cost_usd"] - 0.000158) < 0.00001


def test_cost_estimate_handles_prompt_completion_keys(tmp_path: Path) -> None:
    """JSONLHook may write either {in,out} or {prompt_tokens,completion_tokens}."""
    from datetime import date
    ts = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
    path = tmp_path / "2026-07-26.jsonl"
    _write_event(path, event_type=TelemetryEventType.DRIVER_CHAT_OUT,
                timestamp=ts, driver="A",
                metadata={"token_usage": {"prompt_tokens": 100, "completion_tokens": 50}})

    c = TelemetryConsumer(tmp_path)
    cost = c.cost_estimate(date=date(2026, 7, 26))
    assert cost["total_input_tokens"] == 100
    assert cost["total_output_tokens"] == 50


def test_cost_estimate_skips_events_without_token_usage(tmp_path: Path) -> None:
    from datetime import date
    ts = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
    path = tmp_path / "2026-07-26.jsonl"
    _write_event(path, event_type=TelemetryEventType.DRIVER_CHAT_OUT,
                timestamp=ts, driver="A")  # no token_usage
    _write_event(path, event_type=TelemetryEventType.DRIVER_CHAT_OUT,
                timestamp=ts, driver="A",
                metadata={"token_usage": {"in": 10, "out": 5}})

    c = TelemetryConsumer(tmp_path)
    cost = c.cost_estimate(date=date(2026, 7, 26))
    assert cost["total_input_tokens"] == 10
    assert cost["total_output_tokens"] == 5


# ---------------------------------------------------------------------------
# latency_stats
# ---------------------------------------------------------------------------


def test_latency_stats_per_driver(tmp_path: Path) -> None:
    from datetime import date
    ts = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
    path = tmp_path / "2026-07-26.jsonl"
    _write_event(path, event_type=TelemetryEventType.DRIVER_CHAT_OUT,
                timestamp=ts, driver="A", metadata={"latency_ms": 100})
    _write_event(path, event_type=TelemetryEventType.DRIVER_CHAT_OUT,
                timestamp=ts, driver="A", metadata={"latency_ms": 300})
    _write_event(path, event_type=TelemetryEventType.DRIVER_CHAT_OUT,
                timestamp=ts, driver="B", metadata={"latency_ms": 200})

    c = TelemetryConsumer(tmp_path)
    stats = c.latency_stats(date=date(2026, 7, 26))
    assert stats["A"] == {"count": 2, "min_ms": 100, "max_ms": 300, "avg_ms": 200}
    assert stats["B"] == {"count": 1, "min_ms": 200, "max_ms": 200, "avg_ms": 200}


def test_latency_stats_skips_events_without_latency(tmp_path: Path) -> None:
    from datetime import date
    ts = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
    path = tmp_path / "2026-07-26.jsonl"
    _write_event(path, event_type=TelemetryEventType.DRIVER_CHAT_OUT,
                timestamp=ts, driver="A")  # no latency_ms
    _write_event(path, event_type=TelemetryEventType.STAGE_START,
                timestamp=ts)  # not a driver event

    c = TelemetryConsumer(tmp_path)
    stats = c.latency_stats(date=date(2026, 7, 26))
    assert stats == {}


# ---------------------------------------------------------------------------
# End-to-end: JSONLHook writes, TelemetryConsumer reads
# ---------------------------------------------------------------------------


def test_end_to_end_hook_to_consumer(tmp_path: Path, monkeypatch) -> None:
    """JSONLHook records -> TelemetryConsumer reads the same data."""
    from datetime import date
    monkeypatch.setenv("AGENTOS_TELEMETRY", "on")

    hook = JSONLHook(base_dir=tmp_path)
    hook.record(
        TelemetryEventType.DRIVER_CHAT_OUT,
        driver="OpenClawDriver",
        session_key="task:t-001:stage:research",
        metadata={"latency_ms": 250, "token_usage": {"in": 200, "out": 100}},
    )
    hook.record(
        TelemetryEventType.DRIVER_CHAT_OUT,
        driver="OpenClawDriver",
        session_key="task:t-001:stage:synthesize",
        metadata={"latency_ms": 150, "token_usage": {"in": 300, "out": 150}},
    )

    consumer = TelemetryConsumer(tmp_path)
    s = consumer.summary()
    assert s["total_events"] == 2
    assert s["by_driver"] == {"OpenClawDriver": 2}
    assert s["by_session"]["task:t-001:stage:research"] == 1

    cost = consumer.cost_estimate()
    assert cost["total_input_tokens"] == 500
    assert cost["total_output_tokens"] == 250

    latency = consumer.latency_stats()
    assert latency["OpenClawDriver"]["avg_ms"] == 200
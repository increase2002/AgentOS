"""Telemetry consumer: read + analyze telemetry JSONL files.

Reads events written by JSONLHook (in :mod:`agentos.telemetry.jsonl`).
Provides query, filter, and aggregation methods for evaluation loops
(per ADR-0004).

Refs: ADR-0004 (Evaluation Loop), ADR-0011 (Memory Backend Tiering).
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from agentos.telemetry.jsonl import (
    DEFAULT_TELEMETRY_DIR,
    TelemetryEvent,
    TelemetryEventType,
)


class TelemetryConsumer:
    """Read + analyze telemetry JSONL files."""

    def __init__(self, base_path: Path | str | None = None) -> None:
        self.base_path = (
            Path(base_path) if base_path is not None else DEFAULT_TELEMETRY_DIR
        )

    # ----------------------------------------------------------------- listing

    def list_dates(self) -> list[date]:
        """List dates (as ``date`` objects) that have telemetry files."""
        if not self.base_path.exists():
            return []
        out: list[date] = []
        for f in self.base_path.glob("*.jsonl"):
            try:
                out.append(date.fromisoformat(f.stem))
            except ValueError:
                continue
        return sorted(out)

    def _file_for_date(self, d: date) -> Path:
        return self.base_path / f"{d.isoformat()}.jsonl"

    # ----------------------------------------------------------------- iteration

    def iter_events(
        self,
        *,
        date: date | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        driver: str | None = None,
        event_type: TelemetryEventType | str | None = None,
        session_key: str | None = None,
    ) -> Iterator[TelemetryEvent]:
        """Iterate events with optional filters.

        - ``date=None`` -> all dates; otherwise only that date.
        - ``since`` / ``until`` -> restrict by event timestamp.
        - ``driver`` / ``event_type`` / ``session_key`` -> exact match.
        Filters are AND-combined; None = no filter on that field.
        Corrupt lines are silently skipped (logged upstream by JSONLHook).
        """
        # Pick dates to scan
        if date is not None:
            dates_to_check = [date]
        else:
            dates_to_check = self.list_dates()
            if since is not None:
                since_d = since.date() if isinstance(since, datetime) else since
                dates_to_check = [d for d in dates_to_check if d >= since_d]
            if until is not None:
                until_d = until.date() if isinstance(until, datetime) else until
                dates_to_check = [d for d in dates_to_check if d <= until_d]

        et_value = (
            event_type.value
            if isinstance(event_type, TelemetryEventType)
            else event_type
        )

        for d in dates_to_check:
            path = self._file_for_date(d)
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = TelemetryEvent.model_validate_json(line)
                except Exception:
                    continue
                if driver is not None and event.driver != driver:
                    continue
                if et_value is not None and event.event_type.value != et_value:
                    continue
                if session_key is not None and event.session_key != session_key:
                    continue
                if since is not None and event.timestamp < since:
                    continue
                if until is not None and event.timestamp > until:
                    continue
                yield event

    def list_events(
        self,
        *,
        date: date | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        driver: str | None = None,
        event_type: TelemetryEventType | str | None = None,
        session_key: str | None = None,
    ) -> list[TelemetryEvent]:
        """Same as ``iter_events`` but returns a list (eager)."""
        return list(self.iter_events(
            date=date, since=since, until=until,
            driver=driver, event_type=event_type, session_key=session_key,
        ))

    # ----------------------------------------------------------------- summary

    def summary(
        self,
        *,
        date: date | None = None,
    ) -> dict[str, Any]:
        """Aggregate counts by driver + event_type + session."""
        events = self.list_events(date=date)
        by_driver: Counter[str] = Counter()
        by_event_type: Counter[str] = Counter()
        by_session: Counter[str] = Counter()
        errors = 0
        for e in events:
            by_driver[e.driver or "(none)"] += 1
            by_event_type[e.event_type.value] += 1
            if e.session_key:
                by_session[e.session_key] += 1
            if e.event_type == TelemetryEventType.ERROR:
                errors += 1
        return {
            "total_events": len(events),
            "by_driver": dict(by_driver),
            "by_event_type": dict(by_event_type),
            "by_session": dict(by_session),
            "errors": errors,
        }

    # ----------------------------------------------------------------- cost

    def cost_estimate(
        self,
        *,
        date: date | None = None,
        price_per_1k_input: float = 0.00015,   # gpt-4o-mini default
        price_per_1k_output: float = 0.0006,
    ) -> dict[str, Any]:
        """Estimate token cost from ``DRIVER_CHAT_OUT`` events.

        Reads ``metadata.token_usage`` (which JSONLHook writes as
        ``{"in": ..., "out": ...}``) and aggregates per driver.
        """
        out_events = self.list_events(
            date=date, event_type=TelemetryEventType.DRIVER_CHAT_OUT,
        )
        total_in = 0
        total_out = 0
        by_driver: dict[str, dict[str, int]] = {}
        for e in out_events:
            tu = e.metadata.get("token_usage", {})
            inp = int(tu.get("in") or tu.get("prompt_tokens") or 0)
            out = int(tu.get("out") or tu.get("completion_tokens") or 0)
            total_in += inp
            total_out += out
            drv = e.driver or "(none)"
            slot = by_driver.setdefault(drv, {"in": 0, "out": 0})
            slot["in"] += inp
            slot["out"] += out
        cost = (total_in / 1000.0) * price_per_1k_input + (total_out / 1000.0) * price_per_1k_output
        return {
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "estimated_cost_usd": round(cost, 6),
            "price_per_1k_input_usd": price_per_1k_input,
            "price_per_1k_output_usd": price_per_1k_output,
            "by_driver": by_driver,
        }

    # ----------------------------------------------------------------- latency

    def latency_stats(
        self,
        *,
        date: date | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Per-driver latency stats (min / max / avg) from driver events."""
        driver_events = [
            e for e in self.list_events(date=date)
            if e.event_type in (
                TelemetryEventType.DRIVER_CHAT_IN,
                TelemetryEventType.DRIVER_CHAT_OUT,
            )
        ]
        by_drv_lats: dict[str, list[int]] = {}
        for e in driver_events:
            lat = e.metadata.get("latency_ms")
            if not isinstance(lat, (int, float)):
                continue
            by_drv_lats.setdefault(e.driver or "(none)", []).append(int(lat))
        out: dict[str, dict[str, Any]] = {}
        for drv, lats in by_drv_lats.items():
            if not lats:
                continue
            out[drv] = {
                "count": len(lats),
                "min_ms": min(lats),
                "max_ms": max(lats),
                "avg_ms": sum(lats) // len(lats),
            }
        return out

    # ----------------------------------------------------------------- score

    def score(
        self,
        *,
        date: date | None = None,
        latency_budget_ms: int = 5000,
    ) -> dict[str, dict[str, Any]]:
        """Per-driver composite score from multi-source signals (ADR-0004).

        Aggregates the day's driver events into a 0-1 score per driver using
        three signals (all weighted equally):

        1. **Success rate** — ``DRIVER_CHAT_OUT`` count vs ``ERROR`` count.
           A driver with 9 OUT + 1 ERROR = 0.9 success rate.
        2. **Latency health** — fraction of OUT events whose ``latency_ms``
           is at or below ``latency_budget_ms``. Default budget 5000ms.
        3. **Activity** — soft log-scale bonus so silent drivers don't
           unfairly score zero on tiny samples. Uses
           ``log10(1 + out_count) / log10(1 + max_out_count)``.

        Returns ``{driver_name: {score, signals, sample_size}}``.
        Sample size 0 -> score 0.0 (no data, no signal).
        """
        events = self.list_events(date=date)
        # Bucket events per driver
        out_count: Counter[str] = Counter()
        err_count: Counter[str] = Counter()
        in_latencies: dict[str, list[int]] = {}
        for e in events:
            if e.event_type == TelemetryEventType.DRIVER_CHAT_OUT:
                drv = e.driver or "(none)"
                out_count[drv] += 1
                lat = e.metadata.get("latency_ms")
                if isinstance(lat, (int, float)):
                    in_latencies.setdefault(drv, []).append(int(lat))
            elif e.event_type == TelemetryEventType.ERROR:
                err_count[e.driver or "(none)"] += 1

        drivers = set(out_count) | set(err_count)
        max_out = max(out_count.values(), default=0)
        scores: dict[str, dict[str, Any]] = {}
        for drv in sorted(drivers):
            outs = out_count.get(drv, 0)
            errs = err_count.get(drv, 0)
            if outs == 0 and errs == 0:
                scores[drv] = {"score": 0.0, "signals": {}, "sample_size": 0}
                continue
            total = outs + errs
            success_rate = (outs / total) if total else 0.0
            lats = in_latencies.get(drv, [])
            if lats:
                under_budget = sum(1 for x in lats if x <= latency_budget_ms)
                latency_health = under_budget / len(lats)
            else:
                latency_health = 0.5  # unknown -> neutral
            if max_out > 0:
                import math
                activity = math.log10(1 + outs) / math.log10(1 + max_out)
            else:
                activity = 0.0
            composite = (success_rate + latency_health + activity) / 3.0
            scores[drv] = {
                "score": round(composite, 4),
                "signals": {
                    "success_rate": round(success_rate, 4),
                    "latency_health": round(latency_health, 4),
                    "activity": round(activity, 4),
                },
                "sample_size": total,
            }
        return scores
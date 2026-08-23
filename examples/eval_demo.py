"""ADR-0004 evaluation demo: use TelemetryConsumer to score today's telemetry.

Reads ``G:/AgentOS/telemetry/{today}.jsonl`` and prints:

1. ``summary()`` — counts by driver / event_type / session
2. ``cost_estimate()`` — USD cost from token_usage metadata
3. ``latency_stats()`` — per-driver min/max/avg latency
4. ``score()`` — ADR-0004 multi-signal composite score per driver

Run::

    python examples/eval_demo.py            # today
    python examples/eval_demo.py --date 2026-08-16

No LLM calls — pure file IO + small math. Safe to run on quota-tight days.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agentos.telemetry.consumer import TelemetryConsumer  # noqa: E402


def _print(label: str, payload: dict) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def main() -> int:
    ap = argparse.ArgumentParser(description="ADR-0004 eval demo (no LLM)")
    ap.add_argument(
        "--date", type=str, default=None,
        help="ISO date to analyse (default: today)",
    )
    ap.add_argument(
        "--base", type=Path, default=None,
        help="Telemetry base dir (default: G:/AgentOS/telemetry)",
    )
    ap.add_argument(
        "--latency-budget-ms", type=int, default=5000,
        help="Latency budget for score() (default: 5000)",
    )
    args = ap.parse_args()

    d: date | None = None
    if args.date:
        d = date.fromisoformat(args.date)

    consumer = TelemetryConsumer(base_path=args.base) if args.base else TelemetryConsumer()

    if d is None:
        # pick most recent date with data
        dates = consumer.list_dates()
        if not dates:
            print("(no telemetry files found)")
            return 0
        d = dates[-1]
        print(f"[no --date supplied; using most recent: {d.isoformat()}]")

    _print(f"summary({d.isoformat()})", consumer.summary(date=d))
    _print(f"cost_estimate({d.isoformat()})", consumer.cost_estimate(date=d))
    _print(
        f"latency_stats({d.isoformat()})",
        consumer.latency_stats(date=d),
    )
    _print(
        f"score({d.isoformat()}, latency_budget_ms={args.latency_budget_ms})",
        consumer.score(date=d, latency_budget_ms=args.latency_budget_ms),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

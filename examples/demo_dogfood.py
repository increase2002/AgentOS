"""End-to-end demo: Engine + DAG + fake drivers + telemetry + checkpoint.

Run with:
    AGENTOS_TELEMETRY=on python examples/demo_dogfood.py

This is a self-contained demo showing:
  1. Build a 3-stage DAG (research -> write -> review).
  2. Spin up fake drivers for openclaw / codex / claude.
  3. Engine.run() walks the DAG with concurrency budget.
  4. Telemetry lands in G:/AgentOS/telemetry/{date}.jsonl.
  5. Checkpoint lands in G:/AgentOS/.agentos/checkpoints/.
  6. Re-run with the same task_id; completed stages are skipped.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from agentos.drivers.base import BaseDriver, ChatResult
from agentos.orchestrator.engine import Engine
from agentos.orchestrator.checkpoint import TaskStatus
from agentos.schemas.dag import DAGNode, TaskDAG
from agentos.telemetry import JSONLHook


class EchoDriver(BaseDriver):
    """Fake driver: echoes the brief with a marker showing which agent ran."""

    def __init__(self, name: str, marker: str, latency_ms: int = 50):
        super().__init__(name, {})
        self.marker = marker
        self.latency_ms = latency_ms
        self.calls: list[str] = []

    async def chat(
        self, brief, *, attachments=None, session_key=None, tool_subset=None,
    ):
        self.calls.append(session_key or "")
        await asyncio.sleep(self.latency_ms / 1000)
        return ChatResult(
            content=f"[{self.marker}] {brief}",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        )

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


async def main() -> None:
    # 1. Drivers (fake).
    drivers = {
        "openclaw": EchoDriver("openclaw", "OPENCLAW", latency_ms=80),
        "codex":    EchoDriver("codex", "CODEX", latency_ms=120),
        "claude":   EchoDriver("claude", "CLAUDE", latency_ms=100),
    }

    # 2. Engine.
    engine = Engine(
        drivers=drivers,
        checkpoint_dir=Path("G:/AgentOS/.agentos/checkpoints"),
        concurrency_budget=4,
        telemetry=JSONLHook(),  # default: G:/AgentOS/telemetry
    )

    # 3. DAG: research (parallel x2) -> write -> review.
    dag = TaskDAG(
        task_id="t-demo-001",
        nodes=[
            DAGNode(
                stage_id="research-web",
                agent="openclaw",
                brief_template="Research the web for: {task_brief}",
                expected_artifact_type="research_report",
                parallel_group=1,
            ),
            DAGNode(
                stage_id="research-docs",
                agent="codex",
                brief_template="Research the docs for: {task_brief}",
                expected_artifact_type="research_report",
                parallel_group=1,
            ),
            DAGNode(
                stage_id="write",
                agent="claude",
                brief_template=(
                    "Write a draft based on web findings ({research_web}) "
                    "and docs findings ({research_docs})."
                ),
                inputs=["research-web", "research-docs"],
                expected_artifact_type="draft",
            ),
            DAGNode(
                stage_id="review",
                agent="codex",
                brief_template="Review the draft: {write}",
                inputs=["write"],
                expected_artifact_type="review_notes",
                tool_subset=[],  # plan-only per ADR-0009
            ),
        ],
    )

    # 4. Run.
    print("=== Engine.run t-demo-001 (cold) ===")
    result = await engine.run(
        task_id="t-demo-001",
        brief="AgentOS dogfooding demo",
        dag_payload=dag,
    )
    print(f"  status: {result.status.value}")
    print(f"  total_cost: {result.total_cost}")
    print(f"  stages:")
    for sid, r in result.stages.items():
        print(f"    {sid}: {r.content[:80]}...  ({r.elapsed_ms}ms)")

    # 5. Re-run (partial-success replay).
    print("\n=== Engine.run t-demo-001 (replay) ===")
    result2 = await engine.run(
        task_id="t-demo-001",
        brief="AgentOS dogfooding demo",
        dag_payload=dag,
    )
    print(f"  status: {result2.status.value}")
    print("  driver call counts (should all be 1; replay skips everything):")
    for name, drv in drivers.items():
        print(f"    {name}: {len(drv.calls)} call(s)")

    # 6. Show telemetry + checkpoint files.
    print("\n=== Files ===")
    today = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).date().isoformat()
    tel_path = Path("G:/AgentOS/telemetry") / f"{today}.jsonl"
    if tel_path.exists():
        n = sum(1 for _ in tel_path.read_text(encoding="utf-8").splitlines())
        print(f"  telemetry: {tel_path} ({n} events)")
    ck_path = Path("G:/AgentOS/.agentos/checkpoints/t-demo-001.json")
    if ck_path.exists():
        print(f"  checkpoint: {ck_path} ({ck_path.stat().st_size} bytes)")
    bus_path = Path("G:/AgentOS/.agentos/bus.jsonl")
    if bus_path.exists():
        n = sum(1 for _ in bus_path.read_text(encoding="utf-8").splitlines() if _.strip())
        print(f"  bus: {bus_path} ({n} messages)")


if __name__ == "__main__":
    if os.environ.get("AGENTOS_TELEMETRY", "on") != "off":
        os.environ["AGENTOS_TELEMETRY"] = "on"
    asyncio.run(main())
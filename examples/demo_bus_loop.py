"""End-to-end dogfood demo: BusLoop + Engine + fake drivers.

Demonstrates Plan B in action:
  1. Start a BusLoop + Engine (with fake drivers) listening for
     TASK_REQUEST messages on bus.jsonl.
  2. Send a TASK_REQUEST to ``orchestrator`` via ``agentos send``.
  3. Engine auto-dispatches the DAG, writes TASK_PROGRESS / TASK_ACCEPT
     replies back to the bus.
  4. Watch everything land in telemetry.

Run with::
    AGENTOS_TELEMETRY=on python examples/demo_bus_loop.py

This is what Codex + 老大 + OpenClaw would experience in production:
老大 ferries Codex reply -> bus -> BusLoop -> Engine -> reply -> bus -> 老大 ferries back.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from agentos.bus.jsonl import DEFAULT_BUS_PATH, JSONLBus
from agentos.drivers.base import BaseDriver, ChatResult
from agentos.orchestrator.bus_loop import BusLoop, ORCHESTRATOR_AGENT_NAME
from agentos.orchestrator.engine import Engine
from agentos.schemas.dag import DAGNode, TaskDAG
from agentos.telemetry import JSONLHook


class EchoDriver(BaseDriver):
    """Fake driver: echoes the brief with a marker showing which agent ran."""

    def __init__(self, name: str, marker: str, latency_ms: int = 50):
        super().__init__(name, {})
        self.marker = marker
        self.latency_ms = latency_ms

    async def chat(self, brief, *, attachments=None, session_key=None, tool_subset=None):
        await asyncio.sleep(self.latency_ms / 1000)
        return ChatResult(
            content=f"[{self.marker}] {brief}",
            usage={"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75},
        )

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


def build_demo_dag() -> TaskDAG:
    return TaskDAG(
        task_id="t-busloop-demo",
        nodes=[
            DAGNode(
                stage_id="research",
                agent="openclaw",
                brief_template="Research: {task_brief}",
                expected_artifact_type="report",
            ),
            DAGNode(
                stage_id="synthesize",
                agent="codex",
                brief_template="Synthesize from: {research}",
                inputs=["research"],
                expected_artifact_type="summary",
            ),
        ],
    )


async def main() -> None:
    print("=== Demo: BusLoop + Engine + fake drivers ===\n")

    # 1. Reset bus + checkpoints + telemetry for a clean demo.
    bus_path = Path(DEFAULT_BUS_PATH)
    if bus_path.exists():
        bus_path.unlink()
    ck_dir = Path("G:/AgentOS/.agentos/checkpoints")
    if ck_dir.exists():
        for p in ck_dir.glob("*.json"):
            p.unlink()
    tel_dir = Path("G:/AgentOS/telemetry")
    if tel_dir.exists():
        for p in tel_dir.glob("*.jsonl"):
            p.unlink()

    # 2. Engine + BusLoop.
    engine = Engine(
        drivers={
            "openclaw": EchoDriver("openclaw", "OPENCLAW", latency_ms=80),
            "codex":    EchoDriver("codex", "CODEX", latency_ms=120),
        },
        checkpoint_dir=ck_dir,
        telemetry=JSONLHook(),
    )
    loop = BusLoop(engine, poll_interval_s=0.3)

    print(f"[setup] bus={bus_path}")
    print(f"[setup] engine drivers={list(engine.drivers.keys())}")
    print(f"[setup] BusLoop watching to_agent={loop.watch_to_agent}")
    print()

    # 3. Start the loop in background.
    runner_task = asyncio.create_task(loop.run())

    # Give it a moment to initialize.
    await asyncio.sleep(0.5)

    # 4. Simulate Codex (or any external agent) sending a TASK_REQUEST
    #    directly via JSONLBus — what 老大 would do with `agentos send`
    #    in the real dogfood workflow.
    dag = build_demo_dag()
    bus = JSONLBus()
    from agentos.schemas.message import Message, MessageType, Priority
    import uuid as _uuid
    req = Message(
        id=f"msg-{_uuid.uuid4().hex[:12]}",
        from_agent="codex",
        to_agent=ORCHESTRATOR_AGENT_NAME,
        type=MessageType.TASK_REQUEST,
        priority=Priority.NORMAL,
        payload={
            "task_id": "t-busloop-demo",
            "brief": "Demo bus loop orchestration",
            "dag": dag.model_dump(),
        },
    )
    bus.append(req)
    print(f"[sender] sent {req.id} codex -> {ORCHESTRATOR_AGENT_NAME}")
    print(f"[sender] waiting for Engine to process...\n")

    # 5. Wait for the loop to dispatch + reply.
    await asyncio.sleep(3.0)
    loop.stop()
    try:
        await asyncio.wait_for(runner_task, timeout=2.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        runner_task.cancel()

    # 6. Show results.
    print("\n=== Bus contents ===")
    msgs = bus.iter_messages()
    for rec in msgs:
        print(f"  {rec['id']}  {rec['from_agent']:>11} -> {rec['to_agent']:<11}  "
              f"[{rec['type']}]  {rec['created_at']}")

    print("\n=== Telemetry summary ===")
    today = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).date().isoformat()
    tel_path = tel_dir / f"{today}.jsonl"
    if tel_path.exists():
        lines = tel_path.read_text(encoding="utf-8").splitlines()
        types: dict[str, int] = {}
        for line in lines:
            ev = json.loads(line)
            types[ev["event_type"]] = types.get(ev["event_type"], 0) + 1
        for t, n in sorted(types.items()):
            print(f"  {t}: {n}")

    print("\n=== Checkpoint ===")
    ck = ck_dir / "t-busloop-demo.json"
    if ck.exists():
        cp = json.loads(ck.read_text(encoding="utf-8"))
        print(f"  status: {cp['status']}")
        for sid, st in cp["stages"].items():
            print(f"    {sid}: {st['status']} ({st['retries']} retries)")


if __name__ == "__main__":
    if os.environ.get("AGENTOS_TELEMETRY", "on") != "off":
        os.environ["AGENTOS_TELEMETRY"] = "on"
    asyncio.run(main())
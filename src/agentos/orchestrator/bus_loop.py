"""Bus loop — polls bus.jsonl and dispatches TASK_REQUESTs to Engine.

Listens for ``TASK_REQUEST`` messages addressed to ``orchestrator`` (or
``agentos``) and runs each through the Engine. Emits ``TASK_PROGRESS``
after each stage and ``TASK_ACCEPT`` (with result artifact_ref) on
completion, or ``TASK_BLOCKED`` on failure.

Shared Bus file with the ``agentos`` CLI (Plan A coexistence per ADR-0010).

Message shape
-------------

Inbound (from Codex / humans / external agents)::

    {
      "type": "TASK_REQUEST",
      "from_agent": "codex",
      "to_agent": "orchestrator",
      "payload": {
        "task_id": "t-001",
        "brief": "research and write a Python tutorial",
        "dag": { ... } | null,    # optional pre-built DAG; else Planner fills
      }
    }

Outbound (this module writes back to bus)::

    {
      "type": "TASK_PROGRESS" | "TASK_ACCEPT" | "TASK_BLOCKED",
      "from_agent": "orchestrator",
      "to_agent": "<original requester>",
      "payload": { "task_id": "...", "stage_id": "...", "status": "..." }
    }
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, TYPE_CHECKING

from agentos.bus.jsonl import JSONLBus
from agentos.bus.watch import BusWatcher
from agentos.schemas.message import Message, MessageType, Priority

if TYPE_CHECKING:  # pragma: no cover
    from agentos.orchestrator.engine import Engine

logger = logging.getLogger(__name__)

ORCHESTRATOR_AGENT_NAME = "orchestrator"


# --------------------------------------------------------------------------- #
# BusLoop
# --------------------------------------------------------------------------- #


class BusLoop:
    """Background loop: tail bus, dispatch matching messages to Engine.

    Per ADR-0012 (v0.2), this loop is parameterised for sidecar use:
    ``watch_to_agent`` accepts any agent name, and ``watch_message_types``
    is a list so a single loop can watch multiple message types
    (e.g. OpenClaw sidecar watches KNOWLEDGE_SHARE + REVIEW_REQUEST
    + HANDOFF + TASK_REQUEST).
    """

    def __init__(
        self,
        engine: "Engine",
        *,
        bus: JSONLBus | None = None,
        watch_to_agent: str = ORCHESTRATOR_AGENT_NAME,
        watch_message_types: list[str] | None = None,
        watch_message_type: str | None = None,  # DEPRECATED alias (singular)
        poll_interval_s: float = 1.0,
    ) -> None:
        self.engine = engine
        self.bus = bus or JSONLBus()
        self.watch_to_agent = watch_to_agent
        # Backward compat: default to single TASK_REQUEST (orchestrator mode).
        # If watch_message_type (singular) was passed, use it.
        # If watch_message_types (plural) was passed, use that list.
        if watch_message_types is not None:
            self.watch_message_types: list[str] = list(watch_message_types)
        elif watch_message_type is not None:
            self.watch_message_types = [watch_message_type]
        else:
            self.watch_message_types = [MessageType.TASK_REQUEST.value]
        self.poll_interval_s = poll_interval_s
        self._stop_event: asyncio.Event | None = None

    # ------------------------------------------------------------------ API

    async def run(self) -> None:
        """Run forever (until stop() is called)."""
        self._stop_event = asyncio.Event()
        watcher = BusWatcher(
            self.bus.path,
            self._handle_message,
            to_agent=self.watch_to_agent,
            message_types=self.watch_message_types,
            poll_interval_s=self.poll_interval_s,
        )
        loop_task = asyncio.create_task(self._pump_stop(watcher))
        try:
            await watcher.watch_async()
        finally:
            if not loop_task.done():
                loop_task.cancel()

    def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()

    async def _pump_stop(self, watcher: BusWatcher) -> None:
        assert self._stop_event is not None
        try:
            await self._stop_event.wait()
        except asyncio.CancelledError:
            return
        watcher.stop()

    # ------------------------------------------------------------- dispatch

    def _handle_message(self, msg: Message) -> None:
        """Synchronous handler entry point — schedule async engine.run."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error("bus_loop handler called outside event loop; dropping %s", msg.id)
            return
        loop.create_task(self._dispatch(msg))

    async def _dispatch(self, msg: Message) -> None:
        payload = msg.payload or {}
        task_id = payload.get("task_id") or f"task-{uuid.uuid4().hex[:8]}"
        brief = payload.get("brief") or ""
        dag_payload = payload.get("dag")  # may be None -> Planner fills

        self._publish(
            to_agent=msg.from_agent,
            msg_type=MessageType.TASK_ACCEPT,
            payload={
                "task_id": task_id,
                "status": "accepted",
                "in_reply_to": msg.id,
            },
        )

        try:
            await self.engine.run(
                task_id=task_id,
                brief=brief,
                dag_payload=dag_payload,
            )
            self._publish(
                to_agent=msg.from_agent,
                msg_type=MessageType.TASK_PROGRESS,
                payload={"task_id": task_id, "status": "completed", "in_reply_to": msg.id},
            )
        except Exception as exc:
            logger.exception("engine.run failed for task %s", task_id)
            self._publish(
                to_agent=msg.from_agent,
                msg_type=MessageType.TASK_BLOCKED,
                payload={
                    "task_id": task_id,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                    "in_reply_to": msg.id,
                },
            )

    # ----------------------------------------------------------- publish

    def _publish(
        self,
        *,
        to_agent: str,
        msg_type: MessageType,
        payload: dict,
    ) -> None:
        msg = Message(
            id=f"msg-{uuid.uuid4().hex[:12]}",
            from_agent=ORCHESTRATOR_AGENT_NAME,
            to_agent=to_agent,
            type=msg_type,
            priority=Priority.NORMAL,
            payload=payload,
        )
        try:
            self.bus.append(msg)
            logger.info(
                "bus published %s -> %s id=%s",
                msg_type.value, to_agent, msg.id,
            )
        except Exception:
            logger.exception("failed to publish message to bus")


__all__ = ["BusLoop", "ORCHESTRATOR_AGENT_NAME"]
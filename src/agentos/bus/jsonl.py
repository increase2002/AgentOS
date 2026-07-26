"""JSONL append-only message bus.

Storage: a single JSONL file (one JSON message per line).
Thread-safe append within a process; cross-process safety is best-effort
(relying on POSIX append-atomicity on Linux/macOS and short writes on Windows).

Used by:
- File-bridge watcher (MVP, file-system based coordination)
- Orchestrator Engine (ADR-0010, in-memory + on-disk state source)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Iterator

from agentos.schemas.message import Message, MessageType

DEFAULT_BUS_PATH = Path("G:/AgentOS/.agentos/bus.jsonl")


class JSONLBus:
    """Append-only JSONL message bus."""

    def __init__(self, path: Path = DEFAULT_BUS_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.path.exists():
            self.path.touch()

    def append(self, message: Message, *, artifact_ref: str | None = None) -> None:
        """Append a message. Thread-safe within process."""
        record = {
            "id": message.id,
            "from_agent": message.from_agent,
            "to_agent": message.to_agent,
            "type": message.type.value,
            "priority": message.priority.value,
            "payload": message.payload,
            "created_at": message.created_at.isoformat(),
            "artifact_ref": artifact_ref,
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def iter_messages(
        self,
        *,
        to_agent: str | None = None,
        from_agent: str | None = None,
        message_type: MessageType | None = None,
        since_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Iterate messages with optional filters (AND-combined).

        `since_id`: only yield messages strictly after the message with this id.
        Useful for "give me only new messages since last poll".
        """
        seen_marker = since_id is None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not seen_marker:
                if rec.get("id") == since_id:
                    seen_marker = True
                continue
            if to_agent is not None and rec.get("to_agent") != to_agent:
                continue
            if from_agent is not None and rec.get("from_agent") != from_agent:
                continue
            if message_type is not None and rec.get("type") != message_type.value:
                continue
            yield rec

    def to_agent(
        self, agent: str, *, since_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Shortcut for iter_messages(to_agent=agent)."""
        return list(self.iter_messages(to_agent=agent, since_id=since_id))

    def search(self, query: str) -> list[dict[str, Any]]:
        """Full-text search across payload + id + artifact_ref."""
        results: list[dict[str, Any]] = []
        for rec in self.iter_messages():
            payload_text = json.dumps(rec.get("payload", {}), ensure_ascii=False)
            haystack = " ".join([
                rec.get("id", ""),
                rec.get("from_agent", ""),
                rec.get("to_agent", ""),
                rec.get("type", ""),
                payload_text,
                rec.get("artifact_ref", "") or "",
            ])
            if query in haystack:
                results.append(rec)
        return results

    def count(self) -> int:
        """Total message count."""
        return sum(
            1 for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()
        )

    def clear(self) -> None:
        """Clear the bus (for tests only)."""
        with self._lock:
            self.path.write_text("", encoding="utf-8")

    def summary(self) -> dict[str, int]:
        """Count messages per recipient. Useful for `agentos inbox`."""
        by_agent: dict[str, int] = {}
        for rec in self.iter_messages():
            tgt = rec.get("to_agent", "?")
            by_agent[tgt] = by_agent.get(tgt, 0) + 1
        return by_agent
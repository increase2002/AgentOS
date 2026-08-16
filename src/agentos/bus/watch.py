"""Bus watcher: tail ``bus.jsonl`` and dispatch new messages to a callback.

Design choices
--------------

* **Polling, not inotify.** Cross-platform (Windows + POSIX) without external
  deps. Bus traffic is low (handful of A2A messages per task), so a 1-second
  poll interval is fine.
* **Offset-based delta.** We track the last byte offset we have read; on every
  poll we open the file, ``seek`` to that offset, and parse only the new
  bytes. Cheap, race-free as long as we only read.
* **Append-only assumption.** Bus is append-only JSONL (per ADR-0001 /
  ``docs/03-dogfood-bus.md``), so we never have to "rewind" — if the file is
  truncated/rotated the watcher resets to start-of-file and logs a warning.
* **Thread + async APIs.** Both ``watch()`` (blocking, daemon thread) and
  ``watch_async()`` are provided so consumers can pick what fits.
* **Stop signal.** ``threading.Event`` for the threaded watcher; cancelled
  ``asyncio.Task`` for the async one. ``stop()`` is idempotent.

Used by
-------

* ``agentos watch`` CLI subcommand (老大 ferry acceleration).
* Orchestrator Engine (ADR-0010) — replaces manual ``receive`` polling.
* Telemetry hook (``agentos.telemetry.JSONLHook``) — writes bus message
  arrivals to ``telemetry/{date}.jsonl``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from agentos.schemas.message import Message

logger = logging.getLogger(__name__)

MessageHandler = Callable[[Message], None]


class BusWatcher:
    """Tail a JSONL bus file and dispatch new messages to a handler."""

    def __init__(
        self,
        path: Path,
        handler: MessageHandler,
        *,
        to_agent: str | None = None,
        from_agent: str | None = None,
        message_type: str | None = None,
        message_types: list[str] | None = None,
        poll_interval_s: float = 1.0,
        from_start: bool = False,
    ) -> None:
        """Construct a watcher.

        Parameters
        ----------
        path:
            Bus JSONL file to tail (e.g. ``G:/AgentOS/.agentos/bus.jsonl``).
        handler:
            Callable invoked once per new message that passes the filters.
            Must not raise — wrap internally if needed.
        to_agent / from_agent:
            Optional filters. All AND-combined. ``None`` = no filter.
        message_type:
            Legacy single-type filter. ``None`` = no filter. DEPRECATED —
            prefer ``message_types`` list (ADR-0012, v0.2). When both are
            given, ``message_types`` wins.
        message_types:
            Optional list of message-type strings. Watcher passes messages
            whose ``type`` field is in this list. ``None`` = no type filter.
            Per ADR-0012: sidecars watch multiple types in one BusLoop
            (e.g. KNOWLEDGE_SHARE + REVIEW_REQUEST + HANDOFF + TASK_REQUEST).
        poll_interval_s:
            Seconds between polls. Default 1.0 (hand-tuned for human ferry).
        from_start:
            If ``False`` (default), watcher starts from end-of-file and only
            sees messages appended after launch. If ``True``, replays the
            existing tail (useful for tests / log diggers).
        """
        self.path = Path(path)
        self.handler = handler
        self.to_agent = to_agent
        self.from_agent = from_agent
        # Resolve deprecated `message_type` into new `message_types` list.
        if message_types is not None:
            self.message_types: list[str] | None = list(message_types)
        elif message_type is not None:
            self.message_types = [message_type]
        else:
            self.message_types = None
        self.poll_interval_s = poll_interval_s
        self._stop_event = threading.Event()
        self._offset = 0
        self._seen_ids: set[str] = set()  # de-dup on rotation
        self._from_start = from_start
        self._file_existed_at_init = path.exists()
        self._has_initialised = False

    # ------------------------------------------------------------------ stop

    def stop(self) -> None:
        """Signal the watcher to exit at the next poll. Idempotent."""
        self._stop_event.set()

    # ----------------------------------------------------------- main entry

    def watch(self) -> None:
        """Blocking watch loop. Returns after ``stop()`` is called."""
        if not self.path.exists():
            # Wait for the bus file to appear (orchestrator may start first).
            logger.info("watcher waiting for bus at %s", self.path)
            while not self._stop_event.is_set():
                if self.path.exists():
                    break
                self._stop_event.wait(self.poll_interval_s)
            if self._stop_event.is_set():
                return

        logger.info(
            "watcher started: path=%s to=%s from=%s type=%s interval=%.2fs from_start=%s",
            self.path, self.to_agent, self.from_agent, self.message_types,
            self.poll_interval_s, self._from_start,
        )
        try:
            while not self._stop_event.is_set():
                try:
                    self._poll_once()
                except Exception:
                    logger.exception("watcher poll failed; continuing")
                self._stop_event.wait(self.poll_interval_s)
        finally:
            logger.info("watcher stopped")

    async def watch_async(self) -> None:
        """Async variant. ``stop()`` cancels the loop on next iteration."""
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception:
                logger.exception("watcher poll failed; continuing")
            # Sleep in 100ms slices so stop() takes effect promptly.
            slept = 0.0
            while (
                slept < self.poll_interval_s
                and not self._stop_event.is_set()
            ):
                await asyncio.sleep(min(0.1, self.poll_interval_s - slept))
                slept += 0.1

    # ----------------------------------------------------------------- poll

    def _init_offset(self) -> None:
        """Set starting offset based on whether file existed at init.

        * File existed at watcher startup + ``from_start=False``: jump to
          end-of-file (skip history, only watch future appends).
        * File appeared after watcher startup (``from_start`` irrelevant):
          read from start (we missed nothing because the file did not exist).
        * ``from_start=True``: read from start unconditionally (explicit
          replay).
        """
        if not self.path.exists():
            return
        if self._file_existed_at_init and not self._from_start:
            self._offset = self.path.stat().st_size
        else:
            self._offset = 0

    def _poll_once(self) -> None:
        """Read any new bytes, parse JSONL lines, dispatch handlers."""
        if not self.path.exists():
            self._file_existed_at_init = False
            return
        # First poll: decide starting offset.
        if self._offset == 0 and not self._seen_ids and not self._has_initialised:
            self._init_offset()
            self._has_initialised = True
        self._file_existed_at_init = True  # we see it now
        size = self.path.stat().st_size
        if size < self._offset:
            # Truncated / rotated: reset to start, log once.
            logger.warning(
                "bus file shrank (was %d, now %d); resetting offset",
                self._offset, size,
            )
            self._offset = 0
            self._seen_ids.clear()
        if size == self._offset:
            return  # no new data

        with self.path.open("r", encoding="utf-8") as f:
            f.seek(self._offset)
            new_bytes = f.read()
            self._offset = size  # advance optimistically

        for line in new_bytes.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("skipping malformed bus line: %r", line[:120])
                continue
            self._dispatch(rec)

    def _dispatch(self, rec: dict[str, Any]) -> None:
        msg_id = rec.get("id", "")
        if msg_id in self._seen_ids:
            return  # de-dup
        self._seen_ids.add(msg_id)
        # Cap de-dup set to avoid unbounded growth in long-running watchers.
        if len(self._seen_ids) > 10_000:
            # Keep the most recent half.
            self._seen_ids = set(list(self._seen_ids)[-5_000:])

        if self.to_agent is not None and rec.get("to_agent") != self.to_agent:
            return
        if self.from_agent is not None and rec.get("from_agent") != self.from_agent:
            return
        if self.message_types is not None and rec.get("type") not in self.message_types:
            return

        try:
            msg = Message.model_validate(rec)
        except Exception:
            logger.exception("invalid message record: %r", rec)
            return
        try:
            self.handler(msg)
        except Exception as exc:
            # Log warning only (no traceback) — handlers are user code; we
            # don't want exceptions drowning the watch output.
            logger.warning(
                "handler raised for message %s: %s: %s",
                msg.id, type(exc).__name__, exc,
            )


def iter_new_messages(
    path: Path,
    *,
    start_offset: int = 0,
    stop_event: threading.Event | None = None,
    poll_interval_s: float = 1.0,
) -> Iterator[Message]:
    """Generator variant: yields new messages, stops when ``stop_event`` set.

    Useful for ad-hoc scripts that want to iterate without subclassing.
    """
    handler_calls: list[Message] = []

    class _Collector:
        def __call__(self_inner, m: Message) -> None:
            handler_calls.append(m)

    watcher = BusWatcher(
        path, _Collector(),
        poll_interval_s=poll_interval_s,
    )
    if stop_event is not None:
        # Wrap stop_event to call our watcher's stop when set.
        orig_wait = stop_event.wait

        def wait_then_stop(timeout):
            r = orig_wait(timeout)
            if stop_event.is_set():
                watcher.stop()
            return r

        stop_event.wait = wait_then_stop  # type: ignore[assignment]
    # Run synchronously in current thread; consumer iterates via _drain().
    t = threading.Thread(target=watcher.watch, daemon=True)
    t.start()
    try:
        while not (stop_event and stop_event.is_set()):
            while handler_calls:
                yield handler_calls.pop(0)
            time.sleep(poll_interval_s)
    finally:
        watcher.stop()
        t.join(timeout=2 * poll_interval_s)
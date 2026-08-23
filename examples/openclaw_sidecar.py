"""OpenClaw D2 sidecar (ADR-0012 section 2 + section 5 first dogfood).

Watches the JSONL bus for messages addressed to ``openclaw``, dispatches each
to a real OpenClaw LLM turn (Contract B at
``http://127.0.0.1:18789/v1/chat/completions``), and writes the reply back
to the bus as a ``HANDOFF`` message addressed to the original sender.

This is the "OpenClaw sidecar BusLoop" referenced in ADR-0012 section 5
(first dogfood use case). Once running, all agents — including Codex and
老大 — can reach OpenClaw (me) through the bus with no human ferry.

Usage
-----

::

    # default bus, 5s poll interval, 2 concurrent turns
    python examples/openclaw_sidecar.py

    # dry-run on a temp bus (no real LLM calls)
    python examples/openclaw_sidecar.py --bus /tmp/test_bus.jsonl --dry-run

    # custom concurrency / interval
    python examples/openclaw_sidecar.py --interval 2.0 --concurrency 4

Per ADR-0012 the sidecar is a long-running daemon. Stop with Ctrl-C; the
SIGINT handler drains the in-flight queue before exiting.

References
----------

- ADR-0012 section 2: OpenClaw sidecar abstraction
- ADR-0012 section 5: First dogfood use case (this script)
- ADR-0004: telemetry is auto-emitted by the wrapped OpenClawDriver
- docs/03-dogfood-bus.md: bus protocol + message shapes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

# ensure src/ on path (script may run without editable install)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agentos.bus.watch import BusWatcher  # noqa: E402
from agentos.schemas.message import Message  # noqa: E402

logger = logging.getLogger("openclaw_sidecar")

DEFAULT_BUS = Path(r"G:\AgentOS\.agentos\bus.jsonl")


# --------------------------------------------------------------------------- #
# Bus write helper (delegates to `agentos send` so encoding stays consistent).
# --------------------------------------------------------------------------- #


def _bus_send(
    *,
    to_agent: str,
    from_agent: str,
    text: str,
    task: str,
    msg_type: str = "HANDOFF",
    priority: str = "NORMAL",
) -> None:
    """Append a message to the bus via the ``agentos send`` CLI.

    Why subprocess and not a direct bus write? Two reasons:

    1. Single source of truth for message serialization (CLI validates
       fields, generates ids, timestamps).
    2. Avoids re-implementing the bus schema in this script.
    """
    cmd = [
        "agentos",
        "send",
        "--to", to_agent,
        "--from", from_agent,
        "--type", msg_type,
        "--priority", priority,
        "--text", text,
        "--task", task,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            logger.error(
                "agentos send failed (rc=%d): stderr=%s",
                result.returncode,
                result.stderr.strip()[:500],
            )
        else:
            logger.info("bus sent: %s", result.stdout.strip())
    except FileNotFoundError:
        logger.error("`agentos` CLI not found on PATH; cannot write to bus")
    except subprocess.TimeoutExpired:
        logger.error("agentos send timed out after 30s")


# --------------------------------------------------------------------------- #
# Brief extraction (best-effort across message-type payload shapes).
# --------------------------------------------------------------------------- #


def _build_brief(msg: Message) -> str:
    """Extract a brief string from a Message payload.

    Handles the common payload shapes used by Codex + 老大 + the bus
    CLI: ``text``, ``content`` (str or dict with ``text``), ``subject``
    + ``message`` (Codex HANDOFF style), ``file`` + ``content`` (file
    payload). Falls back to a structured placeholder when nothing
    parseable is present.
    """
    payload: dict[str, Any] = dict(msg.payload or {})

    # Direct text fields
    if isinstance(payload.get("text"), str) and payload["text"].strip():
        return payload["text"]

    # subject + message (Codex HANDOFF)
    if "subject" in payload and "message" in payload:
        return f"{payload['subject']}\n\n{payload['message']}"

    # file + content (Codex attached .md with full body)
    if "file" in payload and isinstance(payload.get("content"), str):
        return payload["content"]

    # content string (older shape)
    content = payload.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str) and text.strip():
            return text

    # Last resort — surface the shape so the LLM has context
    keys = list(payload.keys())
    task_hint = (payload or {}).get("task_id") or msg.id
    return (
        f"[bus msg {msg.id} from {msg.from_agent} type {msg.type.value}, "
        f"task {task_hint}; payload keys: {keys}]"
    )


# --------------------------------------------------------------------------- #
# Per-message handler
# --------------------------------------------------------------------------- #


async def _handle_message(
    msg: Message,
    driver: Any | None,
    semaphore: asyncio.Semaphore,
    *,
    dry_run: bool,
) -> None:
    """Dispatch one bus message: build brief → driver.chat → bus reply."""
    async with semaphore:
        brief = _build_brief(msg)
        # task_id lives inside payload (per cli.py cmd_send layout), with
        # a fallback to the message id so unscoped replies still have a
        # unique session key.
        task_id = (msg.payload or {}).get("task_id") or msg.id
        session_key = f"task:{task_id}:stage:sidecar"
        logger.info(
            "dispatch msg=%s from=%s type=%s task=%s brief_len=%d",
            msg.id, msg.from_agent, msg.type.value, task_id, len(brief),
        )

        if dry_run or driver is None:
            logger.info("[dry-run] would reply with brief: %r", brief[:120])
            return

        try:
            result = await driver.chat(brief, session_key=session_key)
        except Exception as exc:
            logger.exception("chat failed for msg=%s", msg.id)
            _bus_send(
                to_agent=msg.from_agent,
                from_agent="openclaw",
                text=(
                    f"[ERROR] chat failed for msg {msg.id}: "
                    f"{type(exc).__name__}: {str(exc)[:300]}"
                ),
                task=task_id,
                msg_type="HANDOFF",
                priority="HIGH",
            )
            return

        text = getattr(result, "content", "") or ""
        _bus_send(
            to_agent=msg.from_agent,
            from_agent="openclaw",
            text=text,
            task=task_id,
            msg_type="HANDOFF",
        )
        logger.info("replied msg=%s reply_len=%d", msg.id, len(text))


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #


async def _main_async(args: argparse.Namespace) -> int:
    # Lazy import: only needed when not --dry-run. Keeps dry-run zero-deps.
    driver: Any | None = None
    if not args.dry_run:
        from agentos.drivers.openclaw_driver import OpenClawDriver
        from agentos.memory.openclaw_token import resolve_openclaw_token

        token = resolve_openclaw_token()
        if not token:
            logger.error(
                "no OpenClaw token resolved (checked openclaw.json + "
                ".openclaw/gateway.token); cannot run real LLM mode"
            )
            return 1
        driver = OpenClawDriver("sidecar", {"api_key": token})
        logger.info(
            "OpenClaw driver ready (wrapped=%s)",
            getattr(driver, "_agentos_telemetry_wrapped", False),
        )

    semaphore = asyncio.Semaphore(args.concurrency)
    queue: asyncio.Queue[Message] = asyncio.Queue()

    loop = asyncio.get_running_loop()
    stop_future: asyncio.Future[None] = loop.create_future()

    def _signal_stop() -> None:
        if not stop_future.done():
            stop_future.set_result(None)

    # Windows: add_signal_handler is unsupported, fall back to default
    # behaviour (KeyboardInterrupt) + a SIGBREAK handler where available.
    if sys.platform == "win32":
        try:
            signal.signal(signal.SIGINT, lambda *_: _signal_stop())
            signal.signal(signal.SIGBREAK, lambda *_: _signal_stop())  # type: ignore[attr-defined]
        except (ValueError, OSError):
            pass
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_stop)

    # BusWatcher callbacks come from its internal thread; hand off to the
    # event loop via run_in_executor-style scheduling.
    def _enqueue(msg: Message) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, msg)

    watcher = BusWatcher(
        args.bus,
        _enqueue,
        to_agent="openclaw",
        poll_interval_s=args.interval,
        from_start=False,
    )

    consumer_task = asyncio.create_task(
        _consume(queue, driver, semaphore, dry_run=args.dry_run),
        name="sidecar-consumer",
    )

    logger.info(
        "sidecar started: bus=%s interval=%.2fs concurrency=%d dry_run=%s",
        args.bus, args.interval, args.concurrency, args.dry_run,
    )

    # Run the synchronous BusWatcher.watch() in a thread so it can block
    # on its own poll loop while our asyncio loop handles the queue +
    # signals.
    import threading
    watcher_thread = threading.Thread(
        target=watcher.watch, name="sidecar-watcher", daemon=True
    )
    watcher_thread.start()

    try:
        await stop_future
    finally:
        logger.info("stop signal received; draining...")
        watcher.stop()
        # Give consumer up to 10s to drain in-flight messages
        try:
            await asyncio.wait_for(consumer_task, timeout=10)
        except asyncio.TimeoutError:
            consumer_task.cancel()
        watcher_thread.join(timeout=2 * args.interval)
        logger.info("sidecar stopped cleanly")
    return 0


async def _consume(
    queue: asyncio.Queue[Message],
    driver: Any | None,
    semaphore: asyncio.Semaphore,
    *,
    dry_run: bool,
) -> None:
    while True:
        msg = await queue.get()
        try:
            await _handle_message(msg, driver, semaphore, dry_run=dry_run)
        except Exception:
            logger.exception("handler crashed for msg=%s", msg.id)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="OpenClaw D2 sidecar (ADR-0012 section 5)",
    )
    ap.add_argument(
        "--bus", type=Path, default=DEFAULT_BUS,
        help=f"Bus JSONL path (default: {DEFAULT_BUS})",
    )
    ap.add_argument(
        "--interval", type=float, default=5.0,
        help="Bus poll interval in seconds (default: 5.0)",
    )
    ap.add_argument(
        "--concurrency", type=int, default=2,
        help="Max concurrent LLM turns (default: 2)",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Skip real LLM calls; just log what would be dispatched",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    try:
        return asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())

"""OpenClaw multi-LLM dispatcher sidecar (ADR-0012 + ADR-0013 prep).

Watches the JSONL bus for messages addressed to any agent in its dispatch
table and dispatches each to the corresponding LLM backend:

- ``to_agent=openclaw`` -> ``OpenClawDriver`` (Contract B at
  ``http://127.0.0.1:18789/v1/chat/completions``)
- ``to_agent=codex`` -> ``CodexAdapter`` (spawns ``codex`` CLI subprocess per
  ADR-0001)
- unknown ``to_agent`` -> log + skip (extensible: add a new dispatcher to
  ``_build_dispatcher_table``)

Reply is written back to the bus as a ``HANDOFF`` message addressed to the
original sender.

This is the "OpenClaw sidecar BusLoop" referenced in ADR-0012 section 5 +
the dispatcher generalization proposed by Codex on 2026-08-28 (msg-1f8510ffe291)
to give the system a real zero-touch loop while Codex (per-turn agent) cannot
self-poll.

Usage
-----

::

    # default bus, 5s poll interval, 2 concurrent turns
    python examples/openclaw_sidecar.py

    # dry-run on a temp bus (no real LLM calls)
    python examples/openclaw_sidecar.py --bus /tmp/test_bus.jsonl --dry-run

    # custom concurrency / interval / watch list
    python examples/openclaw_sidecar.py --interval 2.0 --concurrency 4 \
        --watch-to openclaw,codex

Per ADR-0012 the sidecar is a long-running daemon. Stop with Ctrl-C; the
SIGINT handler drains the in-flight queue before exiting.

References
----------

- ADR-0012 section 2: OpenClaw sidecar abstraction
- ADR-0012 section 5: First dogfood use case (this script)
- ADR-0013 (in flight): daemon-supervisor (Codex writing the ADR; this script
  is the dispatcher side of it)
- ADR-0001: integration method (Contract B for OpenClaw, subprocess for Codex)
- ADR-0004: telemetry is auto-emitted by the wrapped drivers
- ADR-0007: driver failure policy (fail-fast + retry inherited from drivers)
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

# ensure src/ on path (script may run without editable install)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agentos.bus.watch import BusWatcher  # noqa: E402
from agentos.core.token_bucket import TokenBucket  # noqa: E402
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

    # Last resort -- surface the shape so the LLM has context
    keys = list(payload.keys())
    task_hint = (payload or {}).get("task_id") or msg.id
    return (
        f"[bus msg {msg.id} from {msg.from_agent} type {msg.type.value}, "
        f"task {task_hint}; payload keys: {keys}]"
    )


def _parse_agent_tokens(spec: str) -> dict[str, str]:
    """Parse ``"agent1:tok1,agent2:tok2"`` into a dict.

    Empty / whitespace-only segments are ignored. Used by the CLI to
    convert ``--auth-tokens`` and by env-var loading.
    """
    out: dict[str, str] = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        agent, _, token = part.partition(":")
        agent = agent.strip()
        token = token.strip()
        if agent and token:
            out[agent] = token
    return out


def _verify_auth(msg: Message, expected: dict[str, str]) -> bool:
    """Return True if the message is authenticated for its ``from_agent``.

    Gate is opt-in: when ``expected`` is empty, every message passes.
    Otherwise ``msg.payload["auth_token"]`` must equal
    ``expected[msg.from_agent]`` exactly (string compare). Mismatch is
    logged + rejected (not surfaced on the bus to avoid leaking the
    expected token to a hostile sender).
    """
    if not expected:
        return True
    actual = (msg.payload or {}).get("auth_token")
    want = expected.get(msg.from_agent)
    if want is None:
        # Sender has no registered token; allow through so bootstrap /
        # unconfigured agents aren't blocked. Operators should populate
        # the table before relying on auth.
        return True
    if not isinstance(actual, str) or actual != want:
        return False
    return True


# --------------------------------------------------------------------------- #
# Dispatch table (ADR-0013 prep): route msg.to_agent -> LLM backend
# --------------------------------------------------------------------------- #


@dataclass
class DispatchContext:
    """Per-sidecar resources shared across dispatchers.

    Each dispatcher is an async callable
    ``async def dispatcher(msg, ctx) -> None`` and is responsible for:
      1. Building the brief (via ``_build_brief``)
      2. Calling the appropriate driver (or doing nothing under ``dry_run``)
      3. Writing the reply (or error) back to the bus

    Concurrency gating (semaphore) and error containment live in
    ``_handle_message`` so dispatchers stay focused on their LLM backend.

    ``agent_tokens`` is the per-agent auth gate (ADR-0007). Keys are
    ``from_agent`` names; values are the shared secret each agent must
    echo in ``msg.payload["auth_token"]``. Empty dict disables the gate
    (back-compat with v0.2 messages).
    """

    openclaw_driver: Any | None = None
    codex_adapter: Any | None = None
    dry_run: bool = False
    agent_tokens: dict[str, str] = field(default_factory=dict)


# Type alias for a dispatcher function.
Dispatcher = Callable[[Message, "DispatchContext"], Awaitable[None]]


async def _dispatch_openclaw(msg: Message, ctx: DispatchContext) -> None:
    """Route a message addressed to ``openclaw`` -> OpenClawContractB."""
    brief = _build_brief(msg)
    task_id = (msg.payload or {}).get("task_id") or msg.id
    session_key = f"task:{task_id}:stage:sidecar-openclaw"
    logger.info(
        "dispatch[openclaw] msg=%s from=%s task=%s brief_len=%d",
        msg.id, msg.from_agent, task_id, len(brief),
    )

    if ctx.dry_run or ctx.openclaw_driver is None:
        logger.info("[dry-run] would reply with brief: %r", brief[:120])
        return

    try:
        result = await ctx.openclaw_driver.chat(brief, session_key=session_key)
    except Exception as exc:
        logger.exception("openclaw chat failed for msg=%s", msg.id)
        _bus_send(
            to_agent=msg.from_agent,
            from_agent="openclaw",
            text=(
                f"[ERROR] openclaw chat failed for msg {msg.id}: "
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
    logger.info("replied[openclaw] msg=%s reply_len=%d", msg.id, len(text))


async def _dispatch_codex(msg: Message, ctx: DispatchContext) -> None:
    """Route a message addressed to ``codex`` -> CodexAdapter subprocess.

    This is the zero-touch loop piece: since Codex is a per-turn agent that
    cannot self-poll, OpenClaw sidecar must spawn a Codex CLI subprocess
    on its behalf. Without this dispatcher, Codex becomes unreachable when
    老大 is not actively ferrying.
    """
    brief = _build_brief(msg)
    task_id = (msg.payload or {}).get("task_id") or msg.id
    session_key = f"task:{task_id}:stage:sidecar-codex"
    logger.info(
        "dispatch[codex] msg=%s from=%s task=%s brief_len=%d",
        msg.id, msg.from_agent, task_id, len(brief),
    )

    if ctx.dry_run or ctx.codex_adapter is None:
        logger.info("[dry-run] would spawn codex for brief: %r", brief[:120])
        return

    try:
        result = await ctx.codex_adapter.chat(brief, session_key=session_key)
    except Exception as exc:
        logger.exception("codex chat failed for msg=%s", msg.id)
        _bus_send(
            to_agent=msg.from_agent,
            from_agent="codex",
            text=(
                f"[ERROR] codex chat failed for msg {msg.id}: "
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
        from_agent="codex",  # reply as codex so the bus reflects the source
        text=text,
        task=task_id,
        msg_type="HANDOFF",
    )
    logger.info("replied[codex] msg=%s reply_len=%d", msg.id, len(text))


async def _dispatch_unknown(msg: Message, ctx: DispatchContext) -> None:
    """Fallback: no LLM backend registered for ``msg.to_agent``.

    Per ADR-0013, the dispatch table is extensible; new backends (Anthropic,
    Gemini, etc.) get added by registering a new entry in
    ``_build_dispatcher_table``. Until then we log + skip so a malformed
    message can't kill the sidecar loop.
    """
    logger.warning(
        "no dispatcher for to_agent=%s msg=%s from=%s -- skipping",
        msg.to_agent, msg.id, msg.from_agent,
    )


def _build_dispatcher_table() -> dict[str, Dispatcher]:
    """Return the static dispatch table.

    Order is irrelevant; lookup is by exact ``to_agent`` match.
    """
    return {
        "openclaw": _dispatch_openclaw,
        "codex": _dispatch_codex,
    }


# --------------------------------------------------------------------------- #
# Per-message handler (semaphore + dispatcher routing)
# --------------------------------------------------------------------------- #


async def _handle_message(
    msg: Message,
    dispatchers: dict[str, Dispatcher],
    ctx: DispatchContext,
    semaphore: asyncio.Semaphore,
    rate_limiter: TokenBucket | None,
) -> None:
    """Dispatch one bus message via the dispatcher table.

    The semaphore gates total in-flight LLM turns across all backends so
    we don't blow the 60M/5h shared budget. The optional ``rate_limiter``
    token bucket is checked first (under the semaphore) so an empty
    bucket short-circuits before any LLM call — we reply with a
    [RATE_LIMITED] HANDOFF and the sender can back off instead of us
    paying for a costly 429 from the provider.

    Auth gate (ADR-0007) runs after the rate-limit gate: if
    ``ctx.agent_tokens`` has a token for ``msg.from_agent``, the message
    must carry a matching ``payload["auth_token"]`` or it is dropped
    silently (logged WARN). Failure is not surfaced on the bus to avoid
    leaking expected-token info to a hostile sender.
    """
    async with semaphore:
        # ----- rate limit gate (gates ALL dispatchers) -------------- #
        if rate_limiter is not None and not ctx.dry_run:
            decision = rate_limiter.try_consume()
            if not decision.allowed:
                retry_after_s = max(1.0, round(decision.retry_after_s, 1))
                logger.warning(
                    "rate-limited msg=%s to_agent=%s from=%s retry_after=%.1fs",
                    msg.id, msg.to_agent, msg.from_agent, retry_after_s,
                )
                task_id = (msg.payload or {}).get("task_id") or msg.id
                _bus_send(
                    to_agent=msg.from_agent,
                    from_agent="openclaw",
                    text=(
                        f"[RATE_LIMITED] bucket empty; "
                        f"retry after {retry_after_s:.1f}s "
                        f"(msg {msg.id} to={msg.to_agent})"
                    ),
                    task=task_id,
                    msg_type="HANDOFF",
                    priority="HIGH",
                )
                return

        # ----- auth gate (ADR-0007) --------------------------------- #
        if not _verify_auth(msg, ctx.agent_tokens):
            logger.warning(
                "auth failed for from_agent=%s msg=%s -- dropping",
                msg.from_agent, msg.id,
            )
            return

        dispatcher = dispatchers.get(msg.to_agent)
        if dispatcher is None:
            # Use the fallback directly (it doesn't need an LLM).
            await _dispatch_unknown(msg, ctx)
            return
        try:
            await dispatcher(msg, ctx)
        except Exception:
            # Contain: a single dispatcher crash must not kill the loop.
            logger.exception(
                "dispatcher crashed for msg=%s to_agent=%s",
                msg.id, msg.to_agent,
            )


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #


async def _main_async(args: argparse.Namespace) -> int:
    # Lazy import: only needed when not --dry-run. Keeps dry-run zero-deps.
    openclaw_driver: Any | None = None
    codex_adapter: Any | None = None

    if not args.dry_run:
        # OpenClaw backend (Contract B)
        from agentos.drivers.openclaw_driver import OpenClawDriver
        from agentos.memory.openclaw_token import resolve_openclaw_token

        token = resolve_openclaw_token()
        if not token:
            logger.error(
                "no OpenClaw token resolved (checked openclaw.json + "
                ".openclaw/gateway.token); cannot run real LLM mode"
            )
            return 1
        openclaw_driver = OpenClawDriver("sidecar", {"api_key": token})
        logger.info(
            "OpenClaw driver ready (wrapped=%s)",
            getattr(openclaw_driver, "_agentos_telemetry_wrapped", False),
        )

        # Codex backend (subprocess) -- only if requested via --watch-to codex.
        watch_to_set = {a.strip() for a in args.watch_to.split(",") if a.strip()}
        if "codex" in watch_to_set:
            try:
                from agentos.drivers.codex_adapter import CodexAdapter

                codex_adapter = CodexAdapter("sidecar", {})
                logger.info(
                    "Codex adapter ready (wrapped=%s, invocation=%s)",
                    getattr(codex_adapter, "_agentos_telemetry_wrapped", False),
                    codex_adapter.cli_invocation,
                )
            except Exception as exc:
                logger.error(
                    "Codex adapter init failed: %s: %s -- codex messages "
                    "will be skipped",
                    type(exc).__name__, exc,
                )

    semaphore = asyncio.Semaphore(args.concurrency)
    dispatchers = _build_dispatcher_table()
    # Auth gate (ADR-0007): CLI flag wins, then env var. Empty = off.
    agent_tokens = _parse_agent_tokens(args.auth_tokens)
    if not agent_tokens and os.environ.get("AGENTOS_AUTH_TOKENS"):
        agent_tokens = _parse_agent_tokens(os.environ["AGENTOS_AUTH_TOKENS"])
    if agent_tokens:
        logger.info(
            "auth gate ON for from_agents=%s",
            sorted(agent_tokens.keys()),
        )

    ctx = DispatchContext(
        openclaw_driver=openclaw_driver,
        codex_adapter=codex_adapter,
        dry_run=args.dry_run,
        agent_tokens=agent_tokens,
    )
    rate_limiter = _build_rate_limiter(args, have_llm=not args.dry_run)

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

    # Parse --watch-to into a set; we filter each message in the watcher
    # callback so unknown agents can be logged with full context.
    watch_to_set = {a.strip() for a in args.watch_to.split(",") if a.strip()}

    def _filter(msg: Message) -> None:
        if msg.to_agent in watch_to_set:
            _enqueue(msg)
        # else: silently skip (different sidecar's territory)

    watcher = BusWatcher(
        args.bus,
        _filter,
        to_agent=None,  # we filter ourselves; allow multi-agent watching
        poll_interval_s=args.interval,
        from_start=False,
    )

    consumer_task = asyncio.create_task(
        _consume(queue, dispatchers, ctx, semaphore, rate_limiter),
        name="sidecar-consumer",
    )

    logger.info(
        "sidecar started: bus=%s interval=%.2fs concurrency=%d dry_run=%s "
        "watch_to=%s rate_rpm=%d burst=%d",
        args.bus, args.interval, args.concurrency, args.dry_run,
        sorted(watch_to_set), args.rate_rpm, args.burst,
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
    dispatchers: dict[str, Dispatcher],
    ctx: DispatchContext,
    semaphore: asyncio.Semaphore,
    rate_limiter: TokenBucket | None,
) -> None:
    while True:
        msg = await queue.get()
        try:
            await _handle_message(
                msg, dispatchers, ctx, semaphore, rate_limiter,
            )
        except Exception:
            logger.exception("handler crashed for msg=%s", msg.id)


# --------------------------------------------------------------------------- #
# Rate limiter construction (separate so it's importable for tests).
# --------------------------------------------------------------------------- #


def _build_rate_limiter(
    args: argparse.Namespace,
    *,
    have_llm: bool,
) -> TokenBucket | None:
    """Build the rate-limiter TokenBucket from CLI args.

    Returns None when ``--no-rate-limit`` is set or there is no LLM
    backend (dry-run mode): dry-run does not call the LLM so rate
    limiting is moot, and ``--no-rate-limit`` is an explicit operator
    override for when the bucket is the bottleneck rather than the LLM.
    """
    if getattr(args, "no_rate_limit", False):
        return None
    if not have_llm:
        return None
    refill_rate = args.rate_rpm / 60.0  # tokens per second
    return TokenBucket(capacity=args.burst, refill_rate=refill_rate)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="OpenClaw multi-LLM dispatcher sidecar (ADR-0012 + 0013)",
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
        "--watch-to", type=str, default="openclaw,codex",
        help=(
            "Comma-separated to_agent values this sidecar dispatches for "
            "(default: openclaw,codex). Each sidecar owns a disjoint slice "
            "to prevent double-dispatch."
        ),
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Skip real LLM calls; just log what would be dispatched",
    )
    ap.add_argument(
        "--rate-rpm", type=int, default=60,
        help=(
            "Max LLM-call rate in requests per minute (default: 60). "
            "Token bucket refills at rate_rpm/60 tokens/sec; if the "
            "bucket is empty when a bus message arrives the sidecar "
            "sends a [RATE_LIMITED] HANDOFF instead of calling the LLM. "
            "Applies to ALL dispatchers (openclaw + codex)."
        ),
    )
    ap.add_argument(
        "--burst", type=int, default=10,
        help=(
            "Token-bucket capacity (max burst size, default: 10). "
            "First ``--burst`` bus messages in a row are accepted "
            "without delay; subsequent ones wait for refill."
        ),
    )
    ap.add_argument(
        "--no-rate-limit", action="store_true",
        help=(
            "Disable rate limiting entirely. Useful for tests / "
            "manual replays where the bucket would be the bottleneck."
        ),
    )
    ap.add_argument(
        "--auth-tokens", type=str, default="",
        help=(
            "Per-agent auth tokens (ADR-0007). Format: "
            "'agent1:token1,agent2:token2'. Messages from a configured "
            "agent must carry payload['auth_token']=token or be dropped. "
            "Empty (default) disables the gate. Env var "
            "AGENTOS_AUTH_TOKENS is used as a fallback when this flag "
            "is empty."
        ),
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

"""JSONL telemetry hook — append-only event log on local FS.

The hook records events to ``G:/AgentOS/telemetry/{date}.jsonl``. Each line
is a JSON event with the shape:

.. code-block:: json

    {
      "event_type": "driver_chat_out",
      "timestamp": "2026-07-26T13:04:00+00:00",
      "session_key": "task:t-001:stage:research",
      "driver": "OpenClawDriver",
      "payload": {"brief": "...", "result_preview": "..."},
      "metadata": {"latency_ms": 1234, "token_usage": {"in": 100, "out": 50}}
    }

Event types (``TelemetryEventType``)
------------------------------------

* ``DRIVER_CHAT_IN`` / ``DRIVER_CHAT_OUT`` — wraps ``driver.chat()``
  (async per v0.1 vendor wrappers).
* ``BUS_MESSAGE_IN`` / ``BUS_MESSAGE_OUT`` — wraps bus append / receive.
* ``STAGE_START`` / ``STAGE_END`` — orchestrator stage lifecycle.
* ``ERROR`` — exception summary (stack trace trimmed).

Hook lifecycle
--------------

1. Driver author creates a ``JSONLHook()``.
2. ``wrapped = hook.wrap_driver(driver)`` returns a thin wrapper whose
   ``chat()`` records an IN event before calling ``await driver.chat()``
   and an OUT event after. No mutation of ``ChatResult``.
3. Telemetry writes are wrapped in try/except so a broken log file never
   crashes the driver.

Disable via env: ``AGENTOS_TELEMETRY=off``.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_TELEMETRY_DIR = Path("G:/AgentOS/telemetry")


def is_telemetry_enabled() -> bool:
    """Check the ``AGENTOS_TELEMETRY`` env var (``off`` = disabled)."""
    val = os.environ.get("AGENTOS_TELEMETRY", "on").lower()
    return val not in ("off", "0", "false", "no")


class TelemetryEventType(str, Enum):
    """Telemetry event types (extend as needed)."""

    DRIVER_CHAT_IN = "driver_chat_in"
    DRIVER_CHAT_OUT = "driver_chat_out"
    BUS_MESSAGE_IN = "bus_message_in"
    BUS_MESSAGE_OUT = "bus_message_out"
    STAGE_START = "stage_start"
    STAGE_END = "stage_end"
    ERROR = "error"


class TelemetryEvent(BaseModel):
    """A single telemetry event. One JSON line per file."""

    event_type: TelemetryEventType
    timestamp: datetime
    session_key: str | None = None
    driver: str | None = None
    from_agent: str | None = None
    to_agent: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class JSONLHook:
    """Append-only JSONL telemetry recorder.

    The recorder is cheap to construct (no I/O until ``record()`` is called)
    and safe to share across threads within one process.

    Parameters
    ----------
    base_dir:
        Directory where ``{date}.jsonl`` files live. Default
        ``G:/AgentOS/telemetry``.
    enabled:
        Override the env-var check. ``False`` = no-op.
    """

    def __init__(
        self,
        base_dir: Path | None = None,
        *,
        enabled: bool | None = None,
    ) -> None:
        self.base_dir = Path(base_dir) if base_dir else DEFAULT_TELEMETRY_DIR
        self.enabled = is_telemetry_enabled() if enabled is None else enabled
        self._lock = threading.Lock()
        if self.enabled:
            self.base_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------------- record

    def record(
        self,
        event_type: TelemetryEventType | str,
        *,
        session_key: str | None = None,
        driver: str | None = None,
        from_agent: str | None = None,
        to_agent: str | None = None,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        """Append one event to today's JSONL file. Errors are logged, not raised."""
        if not self.enabled:
            return
        ts = timestamp or datetime.now(timezone.utc)
        if isinstance(event_type, str):
            event_type = TelemetryEventType(event_type)
        event = TelemetryEvent(
            event_type=event_type,
            timestamp=ts,
            session_key=session_key,
            driver=driver,
            from_agent=from_agent,
            to_agent=to_agent,
            payload=payload or {},
            metadata=metadata or {},
        )
        path = self.base_dir / f"{ts.date().isoformat()}.jsonl"
        try:
            line = event.model_dump_json()
            with self._lock:
                with path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            logger.exception("telemetry write failed for event %s", event.event_type)

    # ----------------------------------------------------------- wrap helpers

    def wrap_driver(self, driver: Any) -> Any:
        """Wrap a driver's ``chat()`` so it records IN/OUT events.

        The wrapper preserves the original driver unchanged (same class,
        same methods); only ``chat()`` is intercepted. Returned object
        delegates other attribute access to the wrapped driver.

        Detects async vs sync ``chat()`` automatically (uses ``await`` if
        ``inspect.iscoroutinefunction(driver.chat)``).

        Usage::

            hook = JSONLHook()
            wrapped = hook.wrap_driver(openclaw_driver)
            result = await wrapped.chat(brief, attachments=..., session_key=..., tool_subset=...)
        """
        hook = self
        driver_name = type(driver).__name__
        is_async = inspect.iscoroutinefunction(driver.chat)
        # Capture the original chat callable at wrap time so we can call it
        # directly without re-resolving ``driver.chat`` (which may have been
        # replaced on the instance, e.g. by install_telemetry re-wrapping).
        original_chat = driver.chat

        class _WrappedDriver:
            def __init__(self_inner) -> None:
                self_inner._driver = driver
                self_inner._is_async = is_async

            async def _do_chat_async(self_inner, brief, *, attachments=None, session_key=None, tool_subset=None):
                start = datetime.now(timezone.utc)
                hook.record(
                    TelemetryEventType.DRIVER_CHAT_IN,
                    session_key=session_key,
                    driver=driver_name,
                    payload={"brief": brief, "tool_subset": tool_subset},
                )
                try:
                    result = await original_chat(
                        brief,
                        attachments=attachments,
                        session_key=session_key,
                        tool_subset=tool_subset,
                    )
                except Exception as exc:
                    hook.record(
                        TelemetryEventType.ERROR,
                        session_key=session_key,
                        driver=driver_name,
                        payload={"brief": brief, "error": type(exc).__name__},
                        metadata={"error_msg": str(exc)[:500]},
                    )
                    raise
                latency_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
                meta: dict[str, Any] = {"latency_ms": latency_ms}
                # Capture token usage if driver exposed it (Codex ChatResult uses `usage`).
                usage = getattr(result, "usage", None)
                if usage:
                    meta["token_usage"] = usage
                hook.record(
                    TelemetryEventType.DRIVER_CHAT_OUT,
                    session_key=session_key,
                    driver=driver_name,
                    payload={"brief": brief, "result_preview": _preview(getattr(result, "content", ""))},
                    metadata=meta,
                )
                return result

            def _do_chat_sync(self_inner, brief, *, attachments=None, session_key=None, tool_subset=None):
                start = datetime.now(timezone.utc)
                hook.record(
                    TelemetryEventType.DRIVER_CHAT_IN,
                    session_key=session_key,
                    driver=driver_name,
                    payload={"brief": brief, "tool_subset": tool_subset},
                )
                try:
                    result = original_chat(
                        brief,
                        attachments=attachments,
                        session_key=session_key,
                        tool_subset=tool_subset,
                    )
                except Exception as exc:
                    hook.record(
                        TelemetryEventType.ERROR,
                        session_key=session_key,
                        driver=driver_name,
                        payload={"brief": brief, "error": type(exc).__name__},
                        metadata={"error_msg": str(exc)[:500]},
                    )
                    raise
                latency_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
                meta: dict[str, Any] = {"latency_ms": latency_ms}
                usage = getattr(result, "usage", None) or getattr(result, "token_usage", None)
                if usage:
                    meta["token_usage"] = usage
                hook.record(
                    TelemetryEventType.DRIVER_CHAT_OUT,
                    session_key=session_key,
                    driver=driver_name,
                    payload={"brief": brief, "result_preview": _preview(getattr(result, "content", ""))},
                    metadata=meta,
                )
                return result

            if is_async:
                chat = _do_chat_async
            else:
                chat = _do_chat_sync

            def __getattr__(self_inner, name: str) -> Any:
                return getattr(self_inner._driver, name)

        return _WrappedDriver()

    def wrap_handler(self, handler: Callable[..., Any], event_type: TelemetryEventType | str = TelemetryEventType.BUS_MESSAGE_IN) -> Callable[..., Any]:
        """Wrap a bus-message handler so each invocation records an event."""
        hook = self

        def _wrapped(msg: Any) -> Any:
            hook.record(
                event_type,
                from_agent=getattr(msg, "from_agent", None),
                to_agent=getattr(msg, "to_agent", None),
                payload={"id": getattr(msg, "id", None), "type": str(getattr(msg, "type", ""))},
            )
            return handler(msg)

        return _wrapped


# --------------------------------------------------------------------------- #
# Driver integration helper (ADR-0004 data path)
# --------------------------------------------------------------------------- #

def install_telemetry(
    driver: Any,
    *,
    hook: "JSONLHook | None" = None,
) -> bool:
    """Wire telemetry around ``driver.chat`` in place (ADR-0004).

    Replaces ``driver.chat`` with the JSONL-wrapped equivalent so every call
    emits ``DRIVER_CHAT_IN`` / ``DRIVER_CHAT_OUT`` (or ``ERROR``) events to
    ``G:/AgentOS/telemetry/{date}.jsonl``. Respects the
    ``AGENTOS_TELEMETRY=off`` env var (via ``is_telemetry_enabled``).

    Idempotent: a second call on the same driver is a no-op (detected via
    the ``_agentos_telemetry_wrapped`` sentinel attribute set on the driver).

    Args:
        driver: Any object whose ``chat(brief, *, attachments, session_key,
            tool_subset)`` matches the :class:`BaseDriver` contract. The driver
            does **not** need to subclass :class:`BaseDriver` strictly — the
            helper only uses duck-typed attribute access.
        hook: Optional pre-built :class:`JSONLHook` (used by tests). When
            ``None``, the module-level ``default_hook()`` singleton is used.

    Returns:
        ``True`` if telemetry was installed, ``False`` if skipped (disabled
        by env or already wrapped).

    Note:
        Lives in :mod:`agentos.telemetry.jsonl` (not in
        :mod:`agentos.drivers`) to avoid a circular import — driver
        constructors run while :mod:`agentos.drivers` is still being
        initialized, so the package-level import would fail.
    """
    if not is_telemetry_enabled():
        return False
    if getattr(driver, "_agentos_telemetry_wrapped", False):
        return False
    wrapped = (hook or default_hook()).wrap_driver(driver)
    # Replace the instance attribute (shadows the class method) so existing
    # callers using ``driver.chat(...)`` get telemetry without touching
    # call sites.
    driver.chat = wrapped.chat  # type: ignore[assignment]
    driver._agentos_telemetry_wrapped = True  # type: ignore[attr-defined]
    return True


def _preview(text: str, limit: int = 200) -> str:
    """Truncate text for telemetry payload (avoid huge JSONL files)."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


# --------------------------------------------------------------------------- #
# Module-level singleton (convenience for orchestrator integration)
# --------------------------------------------------------------------------- #

_default_hook: JSONLHook | None = None
_default_hook_lock = threading.Lock()


def default_hook() -> JSONLHook:
    """Return a process-wide singleton ``JSONLHook`` (lazy init)."""
    global _default_hook
    if _default_hook is None:
        with _default_hook_lock:
            if _default_hook is None:
                _default_hook = JSONLHook()
    return _default_hook


__all__ = [
    "DEFAULT_TELEMETRY_DIR",
    "JSONLHook",
    "TelemetryEvent",
    "TelemetryEventType",
    "default_hook",
    "install_telemetry",
    "is_telemetry_enabled",
]
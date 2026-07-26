"""Telemetry hooks for AgentOS.

Records A2A bus events + driver chat events to ``telemetry/{date}.jsonl``
for evaluation loops (ADR-0004) and cost attribution.

Design (per Codex Q-F design accepted 2026-07-25)
--------------------------------------------------

* **Hook pattern, not metadata pollution.** Drivers do not know about
  telemetry; the hook wraps the driver and records events transparently.
* **Append-only JSONL.** One event per line; easy to grep / tail / load
  with pandas.
* **File path by date.** ``G:/AgentOS/telemetry/YYYY-MM-DD.jsonl`` so a
  long-running orchestrator naturally rolls daily.
* **Off by env.** ``AGENTOS_TELEMETRY=off`` disables the hook entirely
  (useful in tests + when running locally for debugging).
* **Thread-safe append.** Single process; cross-process best-effort.
* **Async-aware.** ``wrap_driver`` works with the async driver interface
  (``await driver.chat(...)``) introduced in the v0.1 vendor wrappers.

Used by
-------

* ``JSONLHook`` (this module) — implemented and tested here.
* ``BusWatcher`` (``agentos.bus.watch``) — calls into the hook on each
  new message.
* Orchestrator Engine (ADR-0010) — wraps drivers via ``hook.wrap_driver()``.
* Codex ``src/agentos/telemetry/`` will *consume* these JSONL files for
  evaluation analysis (separate write path).
"""

from agentos.telemetry.jsonl import (
    DEFAULT_TELEMETRY_DIR,
    JSONLHook,
    TelemetryEvent,
    TelemetryEventType,
    default_hook,
    is_telemetry_enabled,
)

__all__ = [
    "DEFAULT_TELEMETRY_DIR",
    "JSONLHook",
    "TelemetryEvent",
    "TelemetryEventType",
    "default_hook",
    "is_telemetry_enabled",
]
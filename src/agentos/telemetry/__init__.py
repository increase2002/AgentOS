"""Telemetry hooks for AgentOS.

* JSONLHook (this package's jsonl.py) -- writes events.
* BusWatcher -- calls into the hook on each bus message.
* Orchestrator Engine (ADR-0010) -- wraps drivers via hook.wrap_driver().
* TelemetryConsumer -- reads + analyzes the JSONL files (eval loop,
  per ADR-0004).
"""

from agentos.telemetry.consumer import TelemetryConsumer
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
    "TelemetryConsumer",
    "TelemetryEvent",
    "TelemetryEventType",
    "default_hook",
    "is_telemetry_enabled",
]
"""Driver layer for external agent integration.

Each external agent (OpenClaw, Codex, Claude, Gemini, ...) gets a Driver that
translates between the agent native protocol and AgentOS unified internal
interface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentos.drivers.anthropic_driver import AnthropicDriver
from agentos.drivers.base import BaseDriver, ChatResult, DriverError
from agentos.drivers.codex_adapter import CodexAdapter
from agentos.drivers.gemini_driver import GeminiDriver
from agentos.drivers.openai_driver import OpenAIDriver
from agentos.drivers.ws_driver import WSDriver
from agentos.telemetry.jsonl import (
    JSONLHook,
    default_hook,
    is_telemetry_enabled,
)

if TYPE_CHECKING:  # pragma: no cover
    from agentos.drivers.base import BaseDriver as _BaseDriver


def install_telemetry(
    driver: "BaseDriver",
    *,
    hook: JSONLHook | None = None,
) -> bool:
    """Wire telemetry around ``driver.chat`` in place (ADR-0004 data path).

    Replaces ``driver.chat`` with the JSONL-wrapped equivalent so every call
    emits ``DRIVER_CHAT_IN`` / ``DRIVER_CHAT_OUT`` (or ``ERROR``) events to
    ``G:/AgentOS/telemetry/{date}.jsonl``. Respects the
    ``AGENTOS_TELEMETRY=off`` env var (via ``is_telemetry_enabled``).

    Idempotent: a second call on the same driver is a no-op (detected via
    ``_agentos_telemetry_wrapped`` sentinel attribute set on the driver).

    Args:
        driver: Any object with an ``async chat(brief, *, attachments, session_key,
            tool_subset)`` method that matches the :class:`BaseDriver` contract.
        hook: Optional pre-built :class:`JSONLHook` (used by tests). When
            ``None``, the module-level ``default_hook()`` singleton is used.

    Returns:
        ``True`` if telemetry was installed, ``False`` if skipped (disabled
        or already wrapped).
    """
    if not is_telemetry_enabled():
        return False
    if getattr(driver, "_agentos_telemetry_wrapped", False):
        return False
    wrapped = (hook or default_hook()).wrap_driver(driver)
    # Replace instance attribute (shadows class method) so existing callers
    # using `driver.chat(...)` get telemetry without changing call sites.
    driver.chat = wrapped.chat  # type: ignore[assignment]
    driver._agentos_telemetry_wrapped = True  # type: ignore[attr-defined]
    return True


__all__ = [
    "AnthropicDriver",
    "BaseDriver",
    "ChatResult",
    "CodexAdapter",
    "DriverError",
    "GeminiDriver",
    "OpenAIDriver",
    "WSDriver",
    "install_telemetry",
]

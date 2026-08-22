"""Driver layer for external agent integration.

Each external agent (OpenClaw, Codex, Claude, Gemini, ...) gets a Driver that
translates between the agent native protocol and AgentOS unified internal
interface.

The ``install_telemetry`` helper lives in
:mod:`agentos.telemetry.jsonl` (not here) to avoid a circular import during
driver construction; this module re-exports it for convenience.
"""

from __future__ import annotations

from agentos.drivers.anthropic_driver import AnthropicDriver
from agentos.drivers.base import BaseDriver, ChatResult, DriverError
from agentos.drivers.codex_adapter import CodexAdapter
from agentos.drivers.gemini_driver import GeminiDriver
from agentos.drivers.openai_driver import OpenAIDriver
from agentos.drivers.ws_driver import WSDriver
from agentos.telemetry.jsonl import install_telemetry

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

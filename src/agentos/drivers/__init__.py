"""Driver layer for external agent integration.

Each external agent (OpenClaw, Codex, Claude, Gemini, ...) gets a Driver that
translates between the agent native protocol and AgentOS unified internal
interface.
"""

from agentos.drivers.base import BaseDriver, ChatResult, DriverError
from agentos.drivers.codex_adapter import CodexAdapter
from agentos.drivers.openai_driver import OpenAIDriver
from agentos.drivers.ws_driver import WSDriver

__all__ = [
    "BaseDriver",
    "ChatResult",
    "CodexAdapter",
    "DriverError",
    "OpenAIDriver",
    "WSDriver",
]
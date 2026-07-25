"""Abstract Driver interface.

Every external agent driver implements this contract. Differences between
agent protocols (HTTP, WS, CLI subprocess, ...) are swallowed inside the
driver; the Orchestrator only sees the unified interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatResult:
    """Standardized result from a driver.chat() call."""

    content: str
    artifact: dict[str, Any] | None = None
    usage: dict[str, int] | None = None  # prompt/completion/total tokens
    metadata: dict[str, Any] = field(default_factory=dict)


class DriverError(Exception):
    """Raised when a driver fails to communicate with its target agent."""


class BaseDriver(ABC):
    """Abstract base for all external agent drivers.

    Drivers are configured per-instance with the connection details of their
    target agent. The Orchestrator instantiates one driver per (agent, host)
    pair and reuses it across tasks.
    """

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        self.name = name
        self.config = config

    @abstractmethod
    async def chat(
        self,
        brief: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
        session_key: str | None = None,
        tool_subset: list[str] | None = None,
    ) -> ChatResult:
        """Send a task brief to the agent and return a ChatResult.

        Args:
            brief: Task brief / system prompt.
            attachments: Optional artifacts (files, structured data) to attach.
            session_key: Optional session identifier for stateful agents.
            tool_subset: Optional whitelist of tools the agent may invoke.
                         e.g. read-only mode = no write/shell tools.
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify the agent endpoint is reachable and authenticated."""
        ...

    async def close(self) -> None:
        """Release any resources (HTTP clients, WS connections)."""
        return None
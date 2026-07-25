"""WebSocket driver for OpenClaw native gateway protocol (Contract A).

Used for OpenClaw node capabilities beyond chat (camera, screen, voice,
node management, cron). For chat-only flows, prefer OpenAIDriver.

Protocol reference: OpenClaw docs/gateway/protocol.md (57KB spec).
"""

from __future__ import annotations

from typing import Any

from agentos.drivers.base import BaseDriver, ChatResult, DriverError


class WSDriver(BaseDriver):
    """Driver for OpenClaw native WebSocket gateway (ws://host:18789)."""

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name, config)
        self.url = config.get("ws_url", "ws://127.0.0.1:18789")
        self.token = config.get("token")
        if not self.token:
            raise DriverError(f"WSDriver[{name}] requires config key: token")

    async def chat(
        self,
        brief: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
        session_key: str | None = None,
        tool_subset: list[str] | None = None,
    ) -> ChatResult:
        # TODO: implement OpenClaw challenge -> connect -> hello-ok handshake
        # then req/resp loop with method=agent.run or appropriate node method.
        # Reference: docs/gateway/protocol.md (Contract A)
        raise DriverError("WSDriver.chat not yet implemented")

    async def health_check(self) -> bool:
        # TODO: open WS, send hello, expect hello-ok within timeout.
        raise DriverError("WSDriver.health_check not yet implemented")
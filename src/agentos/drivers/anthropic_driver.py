"""Anthropic Messages API driver.

Anthropic does NOT have an official OpenAI-compat endpoint. This driver
speaks Anthropic Messages natively and converts to / from the BaseDriver
boundary.

Endpoint: https://api.anthropic.com/v1/messages
Auth: x-api-key: <key> + anthropic-version: 2023-06-01
Default model: claude-sonnet-4-5 (configurable)

NOTE: Verify URL + model availability with老大 before deploying.

Refs: ADR-0001 (Integration Method), ADR-0009 (Tool Subset Enforcement).
"""

from __future__ import annotations

from typing import Any

import httpx

from agentos.drivers.base import BaseDriver, ChatResult, DriverError
from agentos.telemetry.jsonl import install_telemetry


class AnthropicDriver(BaseDriver):
    """Driver for Anthropic Messages API (Claude models)."""

    DEFAULT_BASE_URL = "https://api.anthropic.com"
    DEFAULT_MODEL = "claude-sonnet-4-5"
    ANTHROPIC_VERSION = "2023-06-01"
    DEFAULT_MAX_TOKENS = 4096

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name, config)
        base_url = config.get("base_url", self.DEFAULT_BASE_URL)
        if not isinstance(base_url, str) or not base_url:
            raise DriverError(f"AnthropicDriver[{name}] requires base_url")
        self.base_url = base_url.rstrip("/")
        api_key = config.get("api_key")
        if not api_key:
            raise DriverError(f"AnthropicDriver[{name}] requires api_key")
        self.api_key = api_key
        self.default_model: str = config.get("default_model", self.DEFAULT_MODEL)
        self.max_tokens: int = int(
            config.get("max_tokens", self.DEFAULT_MAX_TOKENS)
        )
        self.timeout_s: float = float(config.get("timeout_s", 60))
        # ADR-0004: auto-wire telemetry hook so every chat() emits
        # DRIVER_CHAT_IN/OUT events to G:/AgentOS/telemetry/{date}.jsonl.
        install_telemetry(self)

    def _build_system_prompt(
        self, tool_subset: list[str] | None
    ) -> str | None:
        """Build the Anthropic `system` field per ADR-0009 (MVP soft constraint)."""
        if tool_subset is None:
            return None
        if tool_subset:
            return (
                f"You may only use these tools: {', '.join(tool_subset)}. "
                "If a request requires a tool not in this list, refuse and "
                "explain why."
            )
        return (
            "You are in plan-only / read-only mode. Do not invoke any tools, "
            "do not write any files. Analyze the request and return a "
            "structured plan only."
        )

    async def _post_json(
        self, path: str, body: dict[str, Any]
    ) -> tuple[dict[str, Any], int]:
        """HTTP POST. Override in tests for mocking."""
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(
                f"{self.base_url}{path}",
                json=body,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": self.ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
            )
        return resp.json(), resp.status_code

    async def chat(
        self,
        brief: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
        session_key: str | None = None,
        tool_subset: list[str] | None = None,
    ) -> ChatResult:
        body: dict[str, Any] = {
            "model": self.default_model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": brief}],
        }
        system = self._build_system_prompt(tool_subset)
        if system is not None:
            body["system"] = system

        data, status = await self._post_json("/v1/messages", body)

        if status != 200:
            raise DriverError(
                f"AnthropicDriver[{self.name}] HTTP {status}: "
                f"{str(data)[:500]}"
            )

        # Parse content blocks (Anthropic returns list of typed blocks)
        content_parts: list[str] = []
        for block in data.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                content_parts.append(block.get("text", ""))

        usage: dict[str, int] | None = None
        u = data.get("usage")
        if isinstance(u, dict):
            inp = int(u.get("input_tokens", 0))
            out = int(u.get("output_tokens", 0))
            usage = {
                "prompt_tokens": inp,
                "completion_tokens": out,
                "total_tokens": inp + out,
            }

        return ChatResult(
            content="".join(content_parts),
            usage=usage,
            metadata={
                "model": data.get("model", self.default_model),
                "stop_reason": data.get("stop_reason"),
                "session_key": session_key,
                "tool_subset": tool_subset,
            },
        )

    async def health_check(self) -> bool:
        # Anthropic has no free health endpoint. Verify config + a quick
        # request could be added later. For MVP, "configured" == "healthy".
        return bool(self.api_key) and bool(self.base_url)
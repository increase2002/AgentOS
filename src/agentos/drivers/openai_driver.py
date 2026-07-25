"""OpenAI-compatible driver.

Works against any agent that exposes an OpenAI-style /v1/chat/completions
endpoint, including:
- OpenClaw (Contract B, requires explicit enable in OpenClaw config)
- OpenAI / Azure OpenAI
- Anthropic via OpenAI-compat proxy
- Google Gemini via OpenAI-compat proxy
- Local llama.cpp / vLLM with OpenAI server
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentos.drivers.base import BaseDriver, ChatResult, DriverError

if TYPE_CHECKING:  # pragma: no cover
    from openai import AsyncOpenAI


class OpenAIDriver(BaseDriver):
    """Driver for any OpenAI-compatible chat completions endpoint."""

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name, config)
        base_url = config.get("base_url")
        api_key = config.get("api_key")
        if not base_url or not api_key:
            raise DriverError(
                f"OpenAIDriver[{name}] requires config keys: base_url, api_key"
            )
        # Lazy import so the package is importable even when the openai SDK
        # is not installed (e.g. running only schema tests in CI).
        from openai import AsyncOpenAI  # noqa: PLC0415

        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.default_model = config.get("default_model", "openclaw")
        # Static headers to attach on every request (e.g. session pinning).
        self.extra_headers: dict[str, str] = config.get("extra_headers", {})

    def _build_messages(
        self,
        brief: str,
        attachments: list[dict[str, Any]] | None = None,
        tool_subset: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the chat-completions messages array from a task brief.

        tool_subset semantics:
          - None  -> no constraint (default; agent may invoke any tool it has)
          - []    -> plan-only / read-only mode; agent must not invoke any tool
          - [...] -> agent may only invoke tools whose names appear in this list

        Enforcement is SOFT in this MVP: the subset is conveyed via a system
        message. Hard enforcement (only the listed tool schemas reach
        `tools=`) requires a tool-schema registry and lands in the next
        iteration. See ADR-0004 (planned).
        """
        messages: list[dict[str, Any]] = []

        if tool_subset is not None:
            if tool_subset:
                tool_list = ", ".join(tool_subset)
                system_msg = (
                    f"You may only use these tools: {tool_list}. "
                    "If a request requires a tool not in this list, refuse and "
                    "explain why."
                )
            else:
                system_msg = (
                    "You are in plan-only / read-only mode. Do not invoke any "
                    "tools, do not write any files. Analyze the request and "
                    "return a structured plan only."
                )
            messages.append({"role": "system", "content": system_msg})

        messages.append({"role": "user", "content": brief})

        if attachments:
            for att in attachments:
                name = att.get("name", "unknown")
                content = att.get("content", "")
                messages.append({
                    "role": "user",
                    "content": f"[attachment:{name}]\n{content}",
                })

        return messages

    async def chat(
        self,
        brief: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
        session_key: str | None = None,
        tool_subset: list[str] | None = None,
    ) -> ChatResult:
        messages = self._build_messages(brief, attachments, tool_subset)

        headers = dict(self.extra_headers)
        if session_key:
            # OpenClaw honors x-openclaw-session-key; other compatible endpoints
            # may ignore unknown headers. The `user` field also acts as a
            # session reuse key for OpenAI-style endpoints.
            headers["x-openclaw-session-key"] = session_key

        try:
            resp = await self.client.chat.completions.create(
                model=self.default_model,
                messages=messages,
                user=session_key,
                extra_headers=headers or None,
            )
        except Exception as exc:  # noqa: BLE001 - surface any provider error
            raise DriverError(
                f"OpenAIDriver[{self.name}] chat failed: {exc}"
            ) from exc

        choice = resp.choices[0]
        usage: dict[str, int] | None = None
        if resp.usage:
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens or 0,
                "completion_tokens": resp.usage.completion_tokens or 0,
                "total_tokens": resp.usage.total_tokens or 0,
            }
        return ChatResult(
            content=choice.message.content or "",
            usage=usage,
            metadata={
                "model": resp.model,
                "finish_reason": choice.finish_reason,
                "session_key": session_key,
                "tool_subset": tool_subset,
            },
        )

    async def health_check(self) -> bool:
        try:
            await self.client.models.list()
            return True
        except Exception:  # noqa: BLE001
            return False

    async def close(self) -> None:
        await self.client.close()
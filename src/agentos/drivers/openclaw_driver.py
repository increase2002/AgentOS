"""OpenClaw driver - Contract B (OpenAI-compatible HTTP) adapter.

OpenClaw exposes an OpenAI-compatible HTTP endpoint at
``/v1/chat/completions`` when ``gateway.http.endpoints.chatCompletions.enabled=true``.
This driver extends :class:`OpenAIDriver` with OpenClaw-specific configuration:

- Default base_url ``http://127.0.0.1:18789/v1``, default model ``openclaw``
  (routes to the gateway default agent).
- ``session_key`` routed via ``x-openclaw-session-key`` header AND the
  standard ``user`` field (OpenClaw honors both for session reuse).
- ``tool_subset`` is enforced via system prompt injection (soft constraint,
  inherited from :class:`OpenAIDriver` per ADR-0007 MVP).
- ``session_key`` validated against OpenClaw reserved prefixes
  (``subagent:`` / ``cron:`` / ``acp:``) per ADR-0006.

Hard constraint (via ``tools=[...]`` schema filtering) requires the upstream
:class:`OpenAIDriver` to grow a ``tools`` parameter handler that reads from
the driver-level ``tool_registry`` config. Tracked in ADR-0007 follow-ups.

Reference: OpenClaw docs/gateway/openai-http-api.md, docs/concepts/session.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentos.drivers.openai_driver import OpenAIDriver
from agentos.schemas.a2a import RESERVED_SESSION_PREFIXES
from agentos.telemetry.jsonl import install_telemetry

if TYPE_CHECKING:  # pragma: no cover
    pass

DEFAULT_BASE_URL = "http://127.0.0.1:18789/v1"
DEFAULT_MODEL = "openclaw"  # routes to gateway default agent
SESSION_HEADER = "x-openclaw-session-key"


class OpenClawDriver(OpenAIDriver):
    """Driver for the OpenClaw local gateway (Contract B).

    Config schema::

        base_url: str            # default: http://127.0.0.1:18789/v1
        api_key: str             # OpenClaw gateway token (required)
        default_model: str       # default: "openclaw"
        session_key_strategy: str  # default: "explicit" (use provided key verbatim)
        tool_registry: dict | None  # for future hard whitelist (ADR-0007 v0.2)

    Example::

        driver = OpenClawDriver("openclaw-main", {
            "api_key": "<gateway-token>",
            # base_url / default_model fall back to OpenClaw defaults
        })
    """

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        # Resolve OpenClaw defaults before delegating to OpenAIDriver's
        # base_url/api_key validation.
        resolved: dict[str, Any] = {
            "base_url": config.get("base_url", DEFAULT_BASE_URL),
            "api_key": config.get("api_key"),
            "default_model": config.get("default_model", DEFAULT_MODEL),
            "extra_headers": dict(config.get("extra_headers", {})),
        }
        if not resolved["api_key"]:
            raise ValueError(
                f"OpenClawDriver[{name}] requires config['api_key'] "
                "(OpenClaw gateway token)"
            )
        super().__init__(name, resolved)
        self.tool_registry: dict[str, dict[str, Any]] | None = config.get(
            "tool_registry"
        )
        self.session_key_strategy: str = config.get(
            "session_key_strategy", "explicit"
        )
        # ADR-0004: auto-wire telemetry hook so every chat() emits
        # DRIVER_CHAT_IN/OUT events to G:/AgentOS/telemetry/{date}.jsonl.
        # Honors AGENTOS_TELEMETRY=off; idempotent under repeated construction.
        install_telemetry(self)

    @staticmethod
    def validate_session_key(session_key: str | None) -> None:
        """Reject session keys that collide with OpenClaw internal namespaces.

        OpenClaw reserves ``subagent:`` / ``cron:`` / ``acp:`` for internal
        subsystems (per docs/concepts/session.md). Driver-level enforcement
        prevents accidental namespace collisions across stages.
        """
        if session_key is None:
            return
        if any(session_key.startswith(p) for p in RESERVED_SESSION_PREFIXES):
            raise ValueError(
                f"OpenClawDriver session_key uses reserved prefix: {session_key} "
                f"(reserved: {', '.join(RESERVED_SESSION_PREFIXES)})"
            )

    async def chat(
        self,
        brief: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
        session_key: str | None = None,
        tool_subset: list[str] | None = None,
    ) -> Any:  # returns OpenAIDriver.ChatResult, kept untyped to avoid import cycle
        """Send a task brief to OpenClaw and return a ChatResult.

        Performs OpenClaw-specific validation on ``session_key`` then delegates
        to :meth:`OpenAIDriver.chat`. The parent enforces ``tool_subset`` via
        system prompt injection (ADR-0007 MVP soft constraint).
        """
        self.validate_session_key(session_key)
        result = await super().chat(
            brief=brief,
            attachments=attachments,
            session_key=session_key,
            tool_subset=tool_subset,
        )
        # Annotate with OpenClaw-specific metadata so the evaluation loop
        # can attribute cost/quality back to the right driver.
        result.metadata["driver"] = "openclaw"
        result.metadata["contract"] = "B"
        result.metadata["base_url"] = self.config["base_url"]
        return result
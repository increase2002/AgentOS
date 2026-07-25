"""OpenClaw JSON5 config schema + loader.

OpenClaw stores its main configuration as ``openclaw.json`` (JSON5 syntax -
supports ``//`` comments, ``/* */`` comments, trailing commas, unquoted
keys). This module captures ONLY the subset of fields AgentOS depends on
for the OpenClaw driver.

For the full OpenClaw schema, see
``docs/gateway/configuration-reference.md``.

This loader is intentionally tolerant:

- Strips JSON5 comments before handing the text to :func:`json.loads`.
- Validates only the fields AgentOS touches (gateway, auth, channels,
  plugins, memory search). Other sections are passed through untouched in
  :attr:`raw` so future driver code can read them without re-parsing.
- Handles the ``__OPEN…ED__`` SecretRef placeholder produced by
  :func:`openclaw config get` by surfacing it as ``None`` and emitting a
  warning so callers don't accidentally treat it as a real token.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SECRET_REF_PLACEHOLDER = "__OPEN…ED__"


class OpenClawConfig(BaseModel):
    """Subset of ``openclaw.json`` that AgentOS depends on."""

    # Gateway binding / port
    gateway_port: int = Field(default=18789, description="Local gateway port")
    gateway_bind: str = Field(
        default="loopback",
        description="loopback | tailnet | 0.0.0.0",
    )

    # Auth
    auth_mode: str = Field(default="token", description="token | password | none")
    auth_token: str | None = Field(
        default=None,
        description="Gateway auth token. None if stored as SecretRef.",
    )
    has_secret_ref_token: bool = Field(
        default=False,
        description="True when the token was elided by openclaw config get.",
    )

    # Contract B (OpenAI-compatible HTTP) - required for OpenClawDriver
    contract_b_enabled: bool = Field(
        default=False,
        description="gateway.http.endpoints.chatCompletions.enabled",
    )
    contract_b_require_auth: bool = Field(
        default=True,
        description=(
            "Always true for security; OpenClaw treats Contract B as full "
            "operator scope (any valid token = admin)."
        ),
    )

    # Topology snapshot (informational; not enforced by the driver)
    channels_enabled: list[str] = Field(default_factory=list)
    plugins_enabled: list[str] = Field(default_factory=list)

    # Memory search defaults - feeds OpenClawMemoryDriver
    memory_search_provider: str = Field(default="openai")
    memory_search_default_model: str = Field(default="text-embedding-3-small")

    # Pass-through of the full document for future drivers
    raw: dict[str, Any] = Field(default_factory=dict)

    def contract_b_url(self, host: str = "127.0.0.1") -> str:
        """Build the Contract B base URL from gateway settings.

        ``host`` defaults to loopback; the driver should pass the resolved
        host (loopback or tailnet IP) explicitly when the bind is not
        loopback.
        """
        return f"http://{host}:{self.gateway_port}/v1"

    def is_contract_b_ready(self) -> bool:
        """Contract B requires explicit enable + a usable token."""
        return self.contract_b_enabled and (
            self.auth_token is not None or self.has_secret_ref_token
        )


def _strip_json5_comments(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments AND trailing commas.

    A minimal JSON5 normaliser sufficient for OpenClaw's config files. It
    preserves content inside string literals and does NOT handle all JSON5
    edge cases (e.g. line continuations inside strings, hex numbers). If a
    future OpenClaw release exercises those, switch to the ``pyjson5``
    package.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                # Preserve the escaped char too (handles \\, \", \n, etc.)
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        # Not in a string
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                # Line comment: skip to (but keep) the newline.
                while i < n and text[i] != "\n":
                    i += 1
                continue
            if nxt == "*":
                # Block comment: skip to closing */.
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i += 2  # step past */
                continue
        out.append(ch)
        i += 1
    cleaned = "".join(out)
    # JSON5 trailing comma: drop a `,` that is only followed by whitespace
    # and a closing brace/bracket.
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    return cleaned


def load_openclaw_config(path: Path | str = Path.home() / ".openclaw" / "openclaw.json") -> OpenClawConfig:
    """Load and validate ``openclaw.json`` (JSON5 syntax).

    Raises:
        FileNotFoundError: if the config file does not exist.
        json.JSONDecodeError: if the file is not valid JSON after comment stripping.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    cleaned = _strip_json5_comments(text)
    data = json.loads(cleaned)
    return parse_openclaw_config(data)


def parse_openclaw_config(data: dict[str, Any]) -> OpenClawConfig:
    """Build an :class:`OpenClawConfig` from a parsed OpenClaw config dict."""
    gateway = data.get("gateway", {}) or {}
    auth = gateway.get("auth", {}) or {}
    raw_token = auth.get("token")

    has_secret_ref = raw_token == SECRET_REF_PLACEHOLDER
    if has_secret_ref:
        logger.warning(
            "OpenClaw config token is stored as a SecretRef (%s); "
            "OpenClawDriver needs the resolved token via env or "
            "OPENCLAW_GATEWAY_TOKEN. Pass api_key explicitly.",
            SECRET_REF_PLACEHOLDER,
        )

    http_endpoints = (gateway.get("http") or {}).get("endpoints") or {}
    chat_completions = http_endpoints.get("chatCompletions") or {}

    memory_search_cfg = (
        (data.get("agents") or {})
        .get("defaults", {})
        .get("memorySearch", {})
        or {}
    )

    return OpenClawConfig(
        gateway_port=gateway.get("port", 18789),
        gateway_bind=gateway.get("bind", "loopback"),
        auth_mode=auth.get("mode", "token"),
        auth_token=None if has_secret_ref else raw_token,
        has_secret_ref_token=has_secret_ref,
        contract_b_enabled=bool(chat_completions.get("enabled", False)),
        contract_b_require_auth=bool(chat_completions.get("requireAuth", True)),
        channels_enabled=sorted((data.get("channels") or {}).keys()),
        plugins_enabled=sorted(
            ((data.get("plugins") or {}).get("entries") or {}).keys()
        ),
        memory_search_provider=memory_search_cfg.get("provider", "openai"),
        memory_search_default_model=memory_search_cfg.get(
            "defaultModel", "text-embedding-3-small"
        ),
        raw=data,
    )
"""Google Gemini driver via OpenAI-compat endpoint.

Google exposes an OpenAI-compatible chat completions endpoint, so this
driver subclasses OpenAIDriver and only overrides the default base_url,
model, and a couple of headers.

Endpoint: https://generativelanguage.googleapis.com/v1beta/openai/
Auth:     Authorization: Bearer <google_api_key>
Default model: gemini-2.0-flash

NOTE: Verify URL + model availability with老大 before deploying.
The base_url has a /v1beta/openai/ path; OpenAIDriver appends
/chat/completions automatically.

Refs: ADR-0001 (Integration Method).
"""

from __future__ import annotations

from typing import Any

from agentos.drivers.openai_driver import OpenAIDriver
from agentos.telemetry.jsonl import install_telemetry


class GeminiDriver(OpenAIDriver):
    """Driver for Google Gemini via OpenAI-compat endpoint."""

    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
    DEFAULT_MODEL = "gemini-2.0-flash"

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        # Merge Gemini-specific defaults before delegating to OpenAIDriver
        merged = {
            "base_url": config.get("base_url", self.DEFAULT_BASE_URL),
            "default_model": config.get("default_model", self.DEFAULT_MODEL),
            **config,
        }
        super().__init__(name, merged)
        # ADR-0004: see agents/drivers/openclaw_driver.py for rationale.
        install_telemetry(self)

    async def health_check(self) -> bool:
        """Verify the Gemini endpoint is reachable.

        Uses the same /models list endpoint that OpenAIDriver.health_check
        uses, but accepts a successful response with or without entries
        (Gemini may return an empty models list when no models match the
        filter, which would falsely fail OpenAIDriver's strict check).
        """
        try:
            resp = await self.client.models.list()
            return resp is not None
        except Exception:
            return False
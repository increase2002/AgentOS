"""OpenClawMemoryAdapter: bridge OpenClawMemoryDriver to MemoryService.

OpenClaw's :class:`OpenClawMemoryDriver` (per ADR-0005 / ADR-0011) returns
``MemorySearchResult(results: list[dict], metadata: dict)`` from its
``search()`` method. The cross-agent :class:`MemoryService` expects
``MemorySearchResult(hits: list[MemoryHit], tier: str)``.

This adapter translates OpenClaw's dict-based result into the unified
``MemoryHit`` shape and declares ``tier="real"`` so MemoryService
normalizes correctly (per ADR-0011 Real tier policy).

Usage::

    from agentos.drivers.openclaw_driver import OpenClawDriver
    from agentos.memory.openclaw_adapter import OpenClawMemoryAdapter
    from agentos.memory import MemoryService

    oc = OpenClawDriver("main", {"base_url": "http://127.0.0.1:18789/v1",
                                 "api_key": token})
    oc_memory = OpenClawMemoryAdapter("openclaw", openclaw_driver=oc)

    service = MemoryService(
        drivers={
            "openclaw": oc_memory,
            "codex":    CodexMemoryDriver("codex"),
            "claude":   AnthropicMemoryDriver("claude"),
            "gemini":   GeminiMemoryDriver("gemini"),
        },
        reranker=GPT4oMiniReranker(),
    )
    hits = await service.search("user authentication design", top_k=5)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentos.memory.base import BaseMemoryDriver, MemoryHit, MemorySearchResult

if TYPE_CHECKING:  # pragma: no cover
    from agentos.drivers.openclaw_memory import (
        OpenClawMemoryDriver,
    )


class OpenClawMemoryAdapter(BaseMemoryDriver):
    """Adapt OpenClawMemoryDriver to BaseMemoryDriver (tier=real).

    Translates dict-based OpenClaw result entries into ``MemoryHit``
    objects. Per ADR-0011, declared tier is ``real`` (OpenClaw has a
    real memory backend; ADR-0005 v0.2 may upgrade to native RPC).
    """

    # Fields to skip when copying into MemoryHit.metadata (they become
    # first-class MemoryHit fields or are reserved keys).
    _RESERVED_KEYS = frozenset({
        "content", "snippet", "score", "source", "id",
    })

    def __init__(
        self,
        name: str,
        *,
        openclaw_driver: "OpenClawMemoryDriver",
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(name=name, tier="real", config=config or {})
        self._driver = openclaw_driver

    async def search(
        self,
        query: str,
        *,
        scope: str = "all",
        top_k: int = 10,
    ) -> MemorySearchResult:
        oc_result = await self._driver.search(query, scope=scope, top_k=top_k)
        hits: list[MemoryHit] = []
        for entry in oc_result.results:
            hits.append(self._entry_to_hit(entry))
        return MemorySearchResult(
            hits=hits,
            driver_name=self.name,
            tier="real",
            metadata=dict(oc_result.metadata),
        )

    def _entry_to_hit(self, entry: dict[str, Any]) -> MemoryHit:
        """Translate one OpenClaw result dict to MemoryHit."""
        content = (
            entry.get("content")
            or entry.get("snippet")
            or entry.get("text")
            or ""
        )
        try:
            score = float(entry.get("score", 0.5))
        except (TypeError, ValueError):
            score = 0.5
        source = str(entry.get("source") or entry.get("id") or "")
        metadata = {
            k: v for k, v in entry.items() if k not in self._RESERVED_KEYS
        }
        return MemoryHit(
            content=content,
            score=score,
            source=source,
            metadata=metadata,
            driver_name=self.name,
            tier="real",
        )
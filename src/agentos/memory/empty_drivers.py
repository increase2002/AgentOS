"""Empty-tier memory drivers for vendors without persistent memory APIs.

Per ADR-0011, Codex / Anthropic / Gemini return empty results + metadata
so MemoryService down-weights their contributions to zero in cross-encoder
rerank. This is honest fabrication-free behavior.
"""

from __future__ import annotations

from typing import Any

from agentos.memory.base import BaseMemoryDriver, MemoryHit, MemorySearchResult


class EmptyMemoryDriver(BaseMemoryDriver):
    """Returns empty MemorySearchResult with 'empty' tier metadata.

    Use for vendors whose public API does not expose a persistent memory
    backend in v0.1. MemoryService treats these hits as zero-score.
    """

    def __init__(self, name: str, config: dict[str, Any] | None = None) -> None:
        super().__init__(name=name, tier="empty", config=config or {})

    async def search(
        self, query: str, *, scope: str = "all", top_k: int = 10
    ) -> MemorySearchResult:
        return MemorySearchResult(
            hits=[],
            driver_name=self.name,
            tier="empty",
            metadata={
                "no_memory_backend": True,
                "reason": (
                    f"{self.name} public API does not expose persistent memory "
                    f"in v0.1; MemoryService down-weights to zero per ADR-0011."
                ),
                "query": query,
                "scope": scope,
                "top_k": top_k,
            },
        )


class CodexMemoryDriver(EmptyMemoryDriver):
    """Empty-tier memory driver for Codex CLI."""


class AnthropicMemoryDriver(EmptyMemoryDriver):
    """Empty-tier memory driver for Anthropic (Claude)."""


class GeminiMemoryDriver(EmptyMemoryDriver):
    """Empty-tier memory driver for Google Gemini."""
"""MemoryService: cross-agent memory federation per ADR-0005 Plan B.

Algorithm (per ADR-0005):
1. Fan-out: concurrent `driver.search(query)` for each registered driver
   (Concurrency Budget per ADR-0006, default 4).
2. Tier handling: down-weight Empty-tier hits to score 0.0 (per ADR-0011).
3. Min-max normalize per driver: each driver produces [0,1] scores
   within its own distribution.
4. Cross-encoder rerank: unified ranking across drivers (default: gpt-4o-mini).
5. Return top_k hits.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agentos.memory.base import BaseMemoryDriver, MemoryHit, MemorySearchResult
from agentos.memory.rerank import CrossEncoderReranker, NullReranker


class MemoryService:
    """Cross-agent memory federation service."""

    def __init__(
        self,
        drivers: dict[str, BaseMemoryDriver],
        *,
        reranker: CrossEncoderReranker | None = None,
        fan_out_concurrency: int = 4,
    ) -> None:
        if not drivers:
            raise ValueError("MemoryService requires at least one driver")
        self.drivers = drivers
        self.reranker: CrossEncoderReranker = reranker or NullReranker()
        self.fan_out_concurrency = fan_out_concurrency

    async def search(
        self,
        query: str,
        *,
        scope: str = "all",
        top_k: int = 10,
    ) -> list[MemoryHit]:
        """Search across all drivers. Returns top-k hits after rerank."""
        if not query:
            return []

        # 1. Fan-out
        sem = asyncio.Semaphore(self.fan_out_concurrency)

        async def search_one(name: str, driver: BaseMemoryDriver) -> tuple[str, Any]:
            async with sem:
                try:
                    return name, await driver.search(query, scope=scope, top_k=top_k * 2)
                except Exception as exc:  # noqa: BLE001
                    return name, exc

        results = await asyncio.gather(
            *(search_one(n, d) for n, d in self.drivers.items())
        )

        # 2. Flatten + filter exceptions
        per_driver: dict[str, MemorySearchResult] = {}
        for name, result in results:
            if isinstance(result, Exception):
                # Driver failed; record and continue.
                continue
            per_driver[name] = result

        all_hits: list[MemoryHit] = []
        for result in per_driver.values():
            all_hits.extend(result.hits)

        if not all_hits:
            return []

        # 3. Min-max normalize per driver (BEFORE down-weighting empty,
        #    so a single-hit empty driver does not get normalized to 1.0).
        for result in per_driver.values():
            hits = result.hits
            if not hits:
                continue
            scores = [h.score for h in hits]
            lo, hi = min(scores), max(scores)
            if hi > lo:
                for h in hits:
                    h.score = (h.score - lo) / (hi - lo)
            # else: keep original scores (all-same driver)

        # 4. Down-weight empty-tier hits to zero (ADR-0011), AFTER normalize.
        for result in per_driver.values():
            if result.tier == "empty":
                for hit in result.hits:
                    hit.score = 0.0

        # 4. Cross-encoder rerank
        reranked = await self.reranker.rerank(query, all_hits, top_k)
        return reranked

    async def close(self) -> None:
        """Close all drivers."""
        for driver in self.drivers.values():
            await driver.close()
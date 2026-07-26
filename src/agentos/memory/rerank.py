"""Cross-encoder rerankers for MemoryService (per ADR-0005)."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentos.memory.base import MemoryHit


class CrossEncoderReranker(ABC):
    """Abstract reranker. Re-scores `hits` against `query` and returns top_k."""

    @abstractmethod
    async def rerank(
        self,
        query: str,
        hits: list["MemoryHit"],
        top_k: int,
    ) -> list["MemoryHit"]:
        ...


class NullReranker(CrossEncoderReranker):
    """No-op reranker. Sorts by score, truncates to top_k.

    Useful for tests + when the caller has pre-normalized scores.
    """

    async def rerank(
        self, query: str, hits: list["MemoryHit"], top_k: int
    ) -> list["MemoryHit"]:
        return sorted(hits, key=lambda h: h.score, reverse=True)[:top_k]


class GPT4oMiniReranker(CrossEncoderReranker):
    """Rerank using OpenAI gpt-4o-mini. Per ADR-0005 MVP.

    Score prompt (per Q-C accepted + Codex semantic-only add):

    ```
    You are a relevance scorer. Score how relevant the document is to the query.
    Output ONLY a single decimal number between 0.0 and 1.0. Nothing else.

    Only consider semantic relevance, not recency or popularity.

    Query: {query}
    Document:
    {content_truncated_500_chars}
    ```

    temperature=0.0, max_tokens=5, parse + clamp [0,1].
    """

    SCORE_PROMPT = (
        "You are a relevance scorer. Score how relevant the document is to the query.\n"
        "Output ONLY a single decimal number between 0.0 and 1.0. Nothing else.\n\n"
        "Only consider semantic relevance, not recency or popularity.\n\n"
        "Query: {query}\n"
        "Document:\n"
        "{content}"
    )

    def __init__(
        self,
        *,
        model: str = "gpt-4o-mini",
        concurrency: int = 4,
        client: object | None = None,
    ) -> None:
        self.model = model
        self.concurrency = concurrency
        self._client = client  # injectable for tests

    async def rerank(
        self, query: str, hits: list["MemoryHit"], top_k: int
    ) -> list["MemoryHit"]:
        # Lazy import so the package works without openai installed (tests).
        from openai import AsyncOpenAI

        client = self._client or AsyncOpenAI()
        sem = asyncio.Semaphore(self.concurrency)

        async def score_one(hit: "MemoryHit") -> "MemoryHit":
            async with sem:
                content = (hit.content or "")[:500]
                prompt = self.SCORE_PROMPT.format(query=query, content=content)
                try:
                    resp = await client.chat.completions.create(
                        model=self.model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.0,
                        max_tokens=5,
                    )
                    text = (resp.choices[0].message.content or "").strip()
                    new_score = float(text)
                    new_score = max(0.0, min(1.0, new_score))
                    hit.score = new_score
                except (ValueError, KeyError, IndexError, AttributeError):
                    # Parse failure / API error: keep original score.
                    pass
                except Exception:
                    # Network / rate limit: keep original score, surface upstream.
                    pass
                return hit

        scored = await asyncio.gather(*(score_one(h) for h in hits))
        return sorted(scored, key=lambda h: h.score, reverse=True)[:top_k]
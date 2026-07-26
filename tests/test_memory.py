"""Tests for the MemoryService + drivers + rerankers (per ADR-0005 + ADR-0011)."""

from __future__ import annotations

from typing import Any

import pytest

from agentos.memory import (
    AnthropicMemoryDriver,
    BaseMemoryDriver,
    CodexMemoryDriver,
    CrossEncoderReranker,
    EmptyMemoryDriver,
    GeminiMemoryDriver,
    MemoryHit,
    MemorySearchResult,
    MemoryService,
    NullReranker,
)
from agentos.memory.rerank import GPT4oMiniReranker


# ---------------------------------------------------------------------------
# BaseMemoryDriver abstract + tier validation
# ---------------------------------------------------------------------------


def test_base_driver_rejects_invalid_tier() -> None:
    class FakeDriver(BaseMemoryDriver):
        async def search(self, query, *, scope="all", top_k=10):
            return MemorySearchResult()

    with pytest.raises(ValueError, match="tier must be"):
        FakeDriver("bad", tier="bogus", config={})


# ---------------------------------------------------------------------------
# Empty-tier drivers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_driver_returns_empty_results() -> None:
    d = CodexMemoryDriver("codex")
    r = await d.search("anything", top_k=10)
    assert r.driver_name == "codex"
    assert r.tier == "empty"
    assert r.hits == []
    assert r.metadata["no_memory_backend"] is True
    assert "v0.1" in r.metadata["reason"]


@pytest.mark.asyncio
async def test_anthropic_and_gemini_empty() -> None:
    for drv in [AnthropicMemoryDriver("claude"), GeminiMemoryDriver("gemini")]:
        r = await drv.search("foo")
        assert r.tier == "empty"
        assert r.hits == []


# ---------------------------------------------------------------------------
# Mock real-tier driver
# ---------------------------------------------------------------------------


class FakeRealDriver(BaseMemoryDriver):
    """Returns hits with mixed scores for normalization testing."""

    def __init__(self, name: str, hits: list[MemoryHit]) -> None:
        super().__init__(name=name, tier="real", config={})
        self._hits = hits

    async def search(self, query, *, scope="all", top_k=10):
        return MemorySearchResult(
            hits=list(self._hits),
            driver_name=self.name,
            tier="real",
            metadata={"echoed_query": query, "echoed_scope": scope},
        )


# ---------------------------------------------------------------------------
# MemoryService fan-out + normalize + rerank
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_requires_at_least_one_driver() -> None:
    with pytest.raises(ValueError, match="at least one driver"):
        MemoryService({})


@pytest.mark.asyncio
async def test_service_fans_out_and_aggregates() -> None:
    openclaw = FakeRealDriver("openclaw", [
        MemoryHit(content="oc-1", score=0.9, source="oc://1", driver_name="openclaw", tier="real"),
        MemoryHit(content="oc-2", score=0.7, source="oc://2", driver_name="openclaw", tier="real"),
    ])
    codex = CodexMemoryDriver("codex")
    svc = MemoryService({"openclaw": openclaw, "codex": codex}, reranker=NullReranker())

    hits = await svc.search("q", top_k=10)
    # 2 from openclaw, 0 from codex (empty tier). Empty hits down-weighted to 0 but still 0 hits.
    assert len(hits) == 2
    assert all(h.driver_name == "openclaw" for h in hits)
    # Min-max normalize: 0.9 -> 1.0, 0.7 -> 0.0
    assert hits[0].score == 1.0
    assert hits[1].score == 0.0


@pytest.mark.asyncio
async def test_service_down_weights_empty_tier_hits() -> None:
    """Empty-tier hits must be down-weighted to 0 even if they accidentally carry scores."""
    class FakeEmptyWithScore(BaseMemoryDriver):
        def __init__(self):
            super().__init__(name="fake", tier="empty", config={})
        async def search(self, query, *, scope="all", top_k=10):
            return MemorySearchResult(
                hits=[MemoryHit(content="x", score=0.99, tier="empty")],
                driver_name="fake",
                tier="empty",
            )

    svc = MemoryService(
        {"fake": FakeEmptyWithScore()},
        reranker=NullReranker(),
    )
    hits = await svc.search("q", top_k=10)
    assert len(hits) == 1
    assert hits[0].score == 0.0  # down-weighted


@pytest.mark.asyncio
async def test_service_normalizes_per_driver() -> None:
    """Each driver's scores are min-max normalized independently."""
    a = FakeRealDriver("a", [
        MemoryHit(content="a1", score=0.5, driver_name="a", tier="real"),
        MemoryHit(content="a2", score=0.9, driver_name="a", tier="real"),
    ])
    b = FakeRealDriver("b", [
        MemoryHit(content="b1", score=0.1, driver_name="b", tier="real"),
        MemoryHit(content="b2", score=0.8, driver_name="b", tier="real"),
    ])
    svc = MemoryService({"a": a, "b": b}, reranker=NullReranker())
    hits = await svc.search("q", top_k=10)

    by_content = {h.content: h.score for h in hits}
    # a: 0.5 -> 0.0, 0.9 -> 1.0
    assert by_content["a1"] == 0.0
    assert by_content["a2"] == 1.0
    # b: 0.1 -> 0.0, 0.8 -> 1.0
    assert by_content["b1"] == 0.0
    assert by_content["b2"] == 1.0


@pytest.mark.asyncio
async def test_service_handles_driver_exception() -> None:
    """A failing driver must not break the whole search."""
    class FailingDriver(BaseMemoryDriver):
        def __init__(self):
            super().__init__(name="boom", tier="real", config={})
        async def search(self, query, *, scope="all", top_k=10):
            raise RuntimeError("driver went down")

    good = FakeRealDriver("good", [
        MemoryHit(content="g1", score=0.5, driver_name="good", tier="real"),
    ])
    svc = MemoryService({"boom": FailingDriver(), "good": good})
    hits = await svc.search("q", top_k=10)
    assert len(hits) == 1
    assert hits[0].driver_name == "good"


@pytest.mark.asyncio
async def test_service_top_k_truncates() -> None:
    openclaw = FakeRealDriver("openclaw", [
        MemoryHit(content=f"hit-{i}", score=0.5 + i * 0.01, driver_name="openclaw", tier="real")
        for i in range(20)
    ])
    svc = MemoryService({"openclaw": openclaw}, reranker=NullReranker())
    hits = await svc.search("q", top_k=5)
    assert len(hits) == 5


@pytest.mark.asyncio
async def test_service_empty_query_returns_empty() -> None:
    openclaw = FakeRealDriver("openclaw", [])
    svc = MemoryService({"openclaw": openclaw})
    assert await svc.search("") == []
    assert await svc.search("   ") == []


# ---------------------------------------------------------------------------
# Rerankers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_reranker_sorts_by_score() -> None:
    hits = [
        MemoryHit(content="low", score=0.2),
        MemoryHit(content="high", score=0.9),
        MemoryHit(content="mid", score=0.5),
    ]
    ranked = await NullReranker().rerank("q", hits, top_k=3)
    assert [h.content for h in ranked] == ["high", "mid", "low"]


@pytest.mark.asyncio
async def test_gpt4o_mini_reranker_with_mock_client() -> None:
    """GPT4oMiniReranker delegates to client.chat.completions.create."""
    from unittest.mock import AsyncMock, MagicMock

    mock_choice = MagicMock()
    mock_choice.message.content = "0.85"
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    reranker = GPT4oMiniReranker(client=mock_client)
    hits = [MemoryHit(content="hello world", score=0.0)]
    ranked = await reranker.rerank("q", hits, top_k=1)

    assert len(ranked) == 1
    assert ranked[0].score == 0.85
    mock_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert call_kwargs["temperature"] == 0.0
    assert call_kwargs["max_tokens"] == 5


@pytest.mark.asyncio
async def test_gpt4o_mini_reranker_clamps_score() -> None:
    from unittest.mock import AsyncMock, MagicMock

    mock_choice = MagicMock()
    mock_choice.message.content = "1.7"  # out of range
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    reranker = GPT4oMiniReranker(client=mock_client)
    ranked = await reranker.rerank("q", [MemoryHit(content="x", score=0.0)], top_k=1)
    assert ranked[0].score == 1.0  # clamped


@pytest.mark.asyncio
async def test_gpt4o_mini_reranker_handles_parse_failure() -> None:
    """When API returns non-numeric, keep original score."""
    from unittest.mock import AsyncMock, MagicMock

    mock_choice = MagicMock()
    mock_choice.message.content = "not a number"
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    reranker = GPT4oMiniReranker(client=mock_client)
    hits = [MemoryHit(content="x", score=0.42)]
    ranked = await reranker.rerank("q", hits, top_k=1)
    assert ranked[0].score == 0.42  # unchanged


@pytest.mark.asyncio
async def test_gpt4o_mini_reranker_truncates_to_500_chars() -> None:
    """Long content should be truncated before sending to the model."""
    from unittest.mock import AsyncMock, MagicMock

    mock_choice = MagicMock()
    mock_choice.message.content = "0.5"
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    reranker = GPT4oMiniReranker(client=mock_client)
    long_content = "x" * 1000
    await reranker.rerank("q", [MemoryHit(content=long_content, score=0.0)], top_k=1)

    # Inspect the prompt sent to the model.
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    sent_content = call_kwargs["messages"][0]["content"]
    # Should contain at most 500 'x's from the doc, plus the surrounding prompt.
    assert sent_content.count("x") <= 500
"""Tests for OpenClawMemoryAdapter (Q3 wire-up fix)."""

from __future__ import annotations

from typing import Any

import pytest

from agentos.memory import (
    BaseMemoryDriver,
    MemoryHit,
    MemorySearchResult,
    OpenClawMemoryAdapter,
)


# ---------------------------------------------------------------------------
# Fake OpenClawMemoryDriver (mirrors src/agentos/drivers/openclaw_memory.py)
# ---------------------------------------------------------------------------


class FakeOpenClawMemoryDriver:
    """Mimics OpenClawMemoryDriver for testing.

    OpenClaw's real OpenClawMemoryDriver.search() returns a dataclass
    MemorySearchResult with `results: list[dict]` and `metadata: dict`.
    We construct that dataclass via duck typing.
    """

    def __init__(self, results: list[dict], metadata: dict | None = None) -> None:
        self._results = results
        self._metadata = metadata or {}
        self.calls: list[dict[str, Any]] = []

    async def search(self, query, *, scope="all", top_k=10):
        self.calls.append({"query": query, "scope": scope, "top_k": top_k})
        return _OCResult(results=list(self._results), metadata=dict(self._metadata))


class _OCResult:
    """Duck-typed OpenClaw MemorySearchResult (results + metadata dicts)."""
    def __init__(self, results: list[dict], metadata: dict):
        self.results = results
        self.metadata = metadata


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translates_results_to_memory_hits() -> None:
    oc = FakeOpenClawMemoryDriver([
        {
            "id": "memory://openclaw/2026-07-25.md#L42",
            "content": "JWT-based session token validation",
            "score": 0.91,
            "source": "memory://openclaw/2026-07-25.md#L42",
            "extra_field": "ignored metadata",  # passed through to metadata
        },
        {
            "content": "OAuth 2.0 with PKCE flow",
            "score": 0.75,
        },
    ])
    adapter = OpenClawMemoryAdapter("openclaw", openclaw_driver=oc)

    result = await adapter.search("user authentication", scope="project", top_k=5)

    assert isinstance(result, MemorySearchResult)
    assert result.tier == "real"
    assert result.driver_name == "openclaw"
    assert len(result.hits) == 2

    h0 = result.hits[0]
    assert isinstance(h0, MemoryHit)
    assert h0.content == "JWT-based session token validation"
    assert h0.score == 0.91
    assert h0.source == "memory://openclaw/2026-07-25.md#L42"
    assert h0.driver_name == "openclaw"
    assert h0.tier == "real"
    assert h0.metadata == {"extra_field": "ignored metadata"}

    h1 = result.hits[1]
    assert h1.content == "OAuth 2.0 with PKCE flow"
    assert h1.score == 0.75
    # Missing source -> empty string
    assert h1.source == ""


@pytest.mark.asyncio
async def test_empty_results_stay_empty() -> None:
    oc = FakeOpenClawMemoryDriver(results=[])
    adapter = OpenClawMemoryAdapter("openclaw", openclaw_driver=oc)
    result = await adapter.search("anything")
    assert result.hits == []
    assert result.tier == "real"


@pytest.mark.asyncio
async def test_metadata_is_forwarded() -> None:
    oc = FakeOpenClawMemoryDriver(
        results=[{"content": "x", "score": 0.5}],
        metadata={
            "hybrid_weights": {"vector": 0.7, "bm25": 0.3},
            "vector_available": True,
        },
    )
    adapter = OpenClawMemoryAdapter("openclaw", openclaw_driver=oc)
    result = await adapter.search("q")
    assert result.metadata == {
        "hybrid_weights": {"vector": 0.7, "bm25": 0.3},
        "vector_available": True,
    }


@pytest.mark.asyncio
async def test_score_defaults_to_half_on_missing_or_invalid() -> None:
    oc = FakeOpenClawMemoryDriver([
        {"content": "no score", "score": None},
        {"content": "bad score", "score": "not-a-number"},
    ])
    adapter = OpenClawMemoryAdapter("openclaw", openclaw_driver=oc)
    result = await adapter.search("q")
    assert result.hits[0].score == 0.5
    assert result.hits[1].score == 0.5


@pytest.mark.asyncio
async def test_query_and_scope_passed_through() -> None:
    oc = FakeOpenClawMemoryDriver([])
    adapter = OpenClawMemoryAdapter("openclaw", openclaw_driver=oc)
    await adapter.search("my query", scope="task:t-001", top_k=3)
    assert oc.calls == [{"query": "my query", "scope": "task:t-001", "top_k": 3}]


def test_adapter_is_base_memory_driver() -> None:
    """Adapter should be usable wherever BaseMemoryDriver is expected."""
    from agentos.memory import BaseMemoryDriver
    oc = FakeOpenClawMemoryDriver([])
    adapter = OpenClawMemoryAdapter("openclaw", openclaw_driver=oc)
    assert isinstance(adapter, BaseMemoryDriver)
    assert adapter.tier == "real"


def test_adapter_name_used_as_driver_name() -> None:
    oc = FakeOpenClawMemoryDriver([])
    a1 = OpenClawMemoryAdapter("main", openclaw_driver=oc)
    a2 = OpenClawMemoryAdapter("openclaw-dev", openclaw_driver=oc)
    assert a1.name == "main"
    assert a2.name == "openclaw-dev"
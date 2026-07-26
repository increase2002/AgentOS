"""Memory driver base + normalized hit / result types.

Per ADR-0011, each driver declares a tier (real / synthetic / empty).
MemoryService uses tier to down-weight empty contributions to zero before
cross-encoder rerank.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryHit:
    """One result from a memory search.

    Scores are 0.0-1.0 after MemoryService normalization. `source` is a
    driver-specific identifier (e.g. 'memory://openclaw/2026-07-25.md#L42').
    """

    content: str
    score: float
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    driver_name: str = ""
    tier: str = ""

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"MemoryHit(driver={self.driver_name!r}, tier={self.tier!r}, "
            f"score={self.score:.3f})"
        )


@dataclass
class MemorySearchResult:
    """Search result from a single driver."""

    hits: list[MemoryHit] = field(default_factory=list)
    driver_name: str = ""
    tier: str = "empty"  # real / synthetic / empty
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseMemoryDriver(ABC):
    """Abstract base for memory search drivers.

    Per ADR-0011, each driver declares its tier at construction time. The
    MemoryService uses this to down-weight empty-tier hits to zero.
    """

    def __init__(self, name: str, tier: str, config: dict[str, Any]) -> None:
        if tier not in ("real", "synthetic", "empty"):
            raise ValueError(f"tier must be real/synthetic/empty, got {tier!r}")
        self.name = name
        self.tier = tier
        self.config = config

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        scope: str = "all",
        top_k: int = 10,
    ) -> MemorySearchResult:
        """Search memory for `query`. Returns hits + metadata."""
        ...

    async def close(self) -> None:
        """Release resources. Default = no-op."""
        return None
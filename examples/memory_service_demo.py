"""MemoryService end-to-end demo (per ADR-0005 + ADR-0011).

Demonstrates the cross-agent memory federation:
- MemoryService.search(query, scope, top_k) fans out to per-driver memory_search()
- Each tier (Real / Synthetic / Empty per ADR-0011) is handled correctly
- Min-max normalize across drivers BEFORE down-weighting Empty tier (per the fix in d1_d2)
- Cross-encoder reranker integration (defaults to NullReranker for cost-free demo)

Default mode: all fake drivers, no LLM calls.
--real mode: OpenClaw backend via Contract B (~20k tokens, opt-in).

Usage:
    python examples/memory_service_demo.py            # fake drivers, 0 tokens
    python examples/memory_service_demo.py --real     # real OpenClaw Contract B
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agentos.drivers.openclaw_memory import MemorySearchResult as OCMemResult
from agentos.memory import (
    AnthropicMemoryDriver,
    CodexMemoryDriver,
    GeminiMemoryDriver,
    MemoryHit,
    MemorySearchResult,
    MemoryService,
    NullReranker,
    OpenClawMemoryAdapter,
)


async def fake_openclaw_results(query: str) -> list[dict]:
    """Mimic OpenClaw returning hybrid search results for the demo."""
    return [
        {"id": "oc-mem-1", "content": f"OpenClaw doc about '{query}': use JWT for auth", "score": 0.91},
        {"id": "oc-mem-2", "content": f"OpenClaw doc about '{query}': rate-limit per agent", "score": 0.74},
    ]


class FakeOpenClawMemoryDriver:
    def __init__(self):
        self.calls = []

    async def search(self, query, *, scope="all", top_k=10):
        self.calls.append({"query": query, "scope": scope, "top_k": top_k})
        results = await fake_openclaw_results(query)
        # Mimic OpenClaw's MemorySearchResult shape: results: list[dict], metadata: dict
        return OCMemResult(results=results, metadata={"hybrid_weights": {"vector": 0.7, "bm25": 0.3}, "vector_available": True})


async def main(args: argparse.Namespace) -> None:
    print("=" * 70)
    print("MemoryService end-to-end demo (ADR-0005 Plan B + ADR-0011 tiering)")
    print("=" * 70)
    print(f"Mode: {'REAL (OpenClaw Contract B)' if args.real else 'FAKE drivers (0 tokens)'}")
    print()

    if args.real:
        try:
            from agentos.drivers.openclaw_driver import OpenClawDriver
            token = Path(r"G:\AgentOS\.openclaw\gateway.token").read_text(encoding="utf-8").strip()
            oc_driver = OpenClawDriver("main", {
                "base_url": "http://127.0.0.1:18789/v1",
                "api_key": token,
                "default_model": "openclaw/default",
            })
        except Exception as e:
            print(f"ERROR: Cannot set up real OpenClaw driver: {e}")
            return
    else:
        oc_driver = FakeOpenClawMemoryDriver()

    oc_memory = OpenClawMemoryAdapter("openclaw", openclaw_driver=oc_driver)
    codex_memory = CodexMemoryDriver("codex")
    claude_memory = AnthropicMemoryDriver("claude")
    gemini_memory = GeminiMemoryDriver("gemini")

    service = MemoryService(
        drivers={
            "openclaw": oc_memory,
            "codex": codex_memory,
            "claude": claude_memory,
            "gemini": gemini_memory,
        },
        reranker=NullReranker(),
    )

    print("MemoryService drivers registered:")
    for name, drv in service.drivers.items():
        print(f"  {name:10s}  tier={drv.tier}")
    print()

    query = args.query
    print(f"Query: {query!r}")
    print(f"Scope: {args.scope!r}  Top-K: {args.top_k}")
    print()

    hits = await service.search(query, scope=args.scope, top_k=args.top_k)

    print(f"Results: {len(hits)} hit(s)")
    print("-" * 70)
    for i, hit in enumerate(hits, 1):
        print(f"  [{i}] tier={hit.tier:7s} score={hit.score:.3f}  driver={hit.driver_name}")
        print(f"      {hit.content}")
    print("-" * 70)


def cli() -> None:
    ap = argparse.ArgumentParser(description="MemoryService end-to-end demo")
    ap.add_argument("--query", default="user authentication",
                    help="Search query (default: 'user authentication')")
    ap.add_argument("--scope", default="all", help="Search scope")
    ap.add_argument("--top-k", type=int, default=5, help="Max results")
    ap.add_argument("--real", action="store_true",
                    help="Use real OpenClaw Contract B (~20k tokens)")
    args = ap.parse_args()
    asyncio.run(main(args))


if __name__ == "__main__":
    cli()

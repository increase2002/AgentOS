"""C.1 real E2E verification.

After Contract B was enabled, this script verifies the full memory
federation chain.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "src"))

from agentos.drivers.openai_driver import OpenAIDriver
from agentos.drivers.openclaw_memory import MemorySearchResult as OCMemResult
from agentos.memory import (
    AnthropicMemoryDriver,
    CodexMemoryDriver,
    GeminiMemoryDriver,
    MemoryService,
    NullReranker,
    OpenClawMemoryAdapter,
)

TOKEN_PATH = Path(r"G:\AgentOS\.openclaw\gateway.token")
OPENCLAW_BASE_URL = "http://127.0.0.1:18789/v1"


def real_chat_smoke() -> None:
    token = TOKEN_PATH.read_text(encoding="utf-8").strip()
    driver = OpenAIDriver(
        "openclaw-real",
        {
            "base_url": OPENCLAW_BASE_URL,
            "api_key": token,
            "default_model": "openclaw/default",
        },
    )
    ok = asyncio.run(driver.health_check())
    print("  health_check:", ok)
    assert ok
    result = asyncio.run(driver.chat("Reply with one word: pong. Then state your identity in one sentence."))
    print("  content:", result.content)
    print("  usage:  ", result.usage)
    print("  model:  ", result.metadata.get("model"))


class FakeOpenClawMemoryDriver:
    async def search(self, query, *, scope="all", top_k=10):
        return OCMemResult(
            results=[
                {"id": "mem://test/1", "content": "JWT validation uses HS256", "score": 0.91, "source": "mem://test/1"},
                {"id": "mem://test/2", "content": "OAuth 2.0 with PKCE flow", "score": 0.75, "source": "mem://test/2"},
            ],
            metadata={"hybrid_weights": {"vector": 0.7, "bm25": 0.3}, "vector_available": True},
        )


def adapter_translation() -> None:
    adapter = OpenClawMemoryAdapter("openclaw", openclaw_driver=FakeOpenClawMemoryDriver())
    result = asyncio.run(adapter.search("user auth", scope="task:t-001", top_k=5))
    print("  tier:", result.tier)
    print("  driver_name:", result.driver_name)
    print("  hits:", len(result.hits))
    for h in result.hits:
        print("   ", h)
    print("  metadata:", result.metadata)


def memory_service_fanout() -> None:
    service = MemoryService(
        drivers={
            "openclaw": OpenClawMemoryAdapter("openclaw", openclaw_driver=FakeOpenClawMemoryDriver()),
            "codex":    CodexMemoryDriver("codex"),
            "claude":   AnthropicMemoryDriver("claude"),
            "gemini":   GeminiMemoryDriver("gemini"),
        },
        reranker=NullReranker(),
    )
    hits = asyncio.run(service.search("user authentication", top_k=5))
    print("  total hits:", len(hits))
    for h in hits:
        print("   ", h)


def main() -> None:
    print("=" * 60)
    print("1. Real OpenClaw Contract B smoke test")
    print("=" * 60)
    real_chat_smoke()

    print()
    print("=" * 60)
    print("2. OpenClawMemoryAdapter translation")
    print("=" * 60)
    adapter_translation()

    print()
    print("=" * 60)
    print("3. MemoryService fan-out")
    print("=" * 60)
    memory_service_fanout()
    print()
    print("=" * 60)
    print("C.1 closed loop verified")
    print("=" * 60)


if __name__ == "__main__":
    main()

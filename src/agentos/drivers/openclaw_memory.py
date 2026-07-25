"""OpenClaw memory_search driver.

OpenClaw's ``memory_search`` is an agent-internal tool (per
``docs/concepts/memory-search.md``) and is NOT exposed directly via the
OpenAI-compatible HTTP endpoint. To make it accessible to AgentOS, this
driver uses OpenClaw's WebSocket gateway protocol (Contract A) to invoke a
specialised "memory-searcher" agent that runs ``memory_search`` and returns
structured results.

.. important::

   **MVP LIMITATION** (ADR-0005 / discussed 2026-07-25):

   Every call to :meth:`OpenClawMemoryDriver.search` triggers a full agent
   invocation, which adds roughly one LLM round-trip of latency and the
   associated token cost. This is acceptable for MVP where query volume is
   low, but it does NOT scale.

   **v0.2 plan**: advocate for OpenClaw to expose a native
   ``memory.search`` RPC method via Contract A (WebSocket protocol). That
   will let this driver return raw hybrid-search results without an
   intervening LLM call.

   See ``docs/gateway/protocol.md`` for the WebSocket protocol spec and
   the ``hello-ok.features.methods`` list (negotiated at handshake time).

The MVP implementation deliberately returns an empty result with diagnostic
metadata rather than guess at the WS method name, so callers can ship
end-to-end flows while the native RPC is negotiated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemorySearchResult:
    """Normalised memory search result.

    Same shape regardless of which underlying agent ran the search. The
    orchestrator's :class:`MemoryService` fans out to multiple drivers and
    merges these results (see ADR-0005, B方案).
    """

    results: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class OpenClawMemoryDriver:
    """Memory search driver for OpenClaw.

    Config schema::

        gateway_url: str   # default: ws://127.0.0.1:18789
        token: str         # OpenClaw gateway token (required)
        search_agent: str  # default: "memory-searcher" (must be a configured agent)

    The MVP returns an empty result set with metadata describing the
    deferred native RPC plan. Replace :meth:`search` with a real WebSocket
    implementation (per ``docs/gateway/protocol.md``) when OpenClaw exposes
    a native ``memory.search`` RPC method.
    """

    DEFAULT_GATEWAY_URL = "ws://127.0.0.1:18789"
    DEFAULT_SEARCH_AGENT = "memory-searcher"

    def __init__(self, config: dict[str, Any]) -> None:
        self.gateway_url: str = config.get("gateway_url", self.DEFAULT_GATEWAY_URL)
        self.token: str | None = config.get("token")
        self.search_agent: str = config.get("search_agent", self.DEFAULT_SEARCH_AGENT)
        if not self.token:
            raise ValueError(
                "OpenClawMemoryDriver requires config['token'] "
                "(OpenClaw gateway token)"
            )

    async def search(
        self,
        query: str,
        scope: str = "all",
        top_k: int = 10,
    ) -> MemorySearchResult:
        """Search OpenClaw memory and return normalised results.

        MVP: returns an empty result set with metadata documenting the
        deferred native RPC implementation. See module docstring.
        """
        return MemorySearchResult(
            results=[],
            metadata={
                "source": "openclaw",
                "query": query,
                "scope": scope,
                "top_k": top_k,
                "status": "mvp_stub",
                "implementation_note": (
                    "MVP via sessions_send not yet wired. Tracked for v0.2: "
                    "negotiate native memory.search RPC with OpenClaw team. "
                    "Until then, callers should fall back to direct "
                    "memory_search tool invocation via OpenClaw sessions."
                ),
            },
        )
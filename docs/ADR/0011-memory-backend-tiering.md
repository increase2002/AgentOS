# ADR-0011: Memory Backend Tiering (Real / Synthetic / Empty)

- **Status**: Accepted
- **Date**: 2026-07-25
- **Deciders**: Codex, OpenClaw (龙大), Increase (老大)

## Context

ADR-0005 specifies how cross-agent memory federation works at the Orchestrator level (fan-out + normalize + cross-encoder rerank). It does not, however, classify what each individual agent's memory backend looks like. In practice the agents we plan to integrate in v0.1 have very different native memory capabilities:

- **OpenClaw**: rich local memory store (SQLite + FTS5 + sqlite-vec, 11 embedding providers). Real, queryable backend.
- **Codex CLI**: no native persistent memory between sessions. Each invocation starts with the brief; nothing to query later.
- **Claude (Anthropic Messages)**: no native persistent memory API. Projects are stateless from the API surface; any "memory" lives in the caller's prompt.
- **Gemini**: same as Claude — no first-party memory_search API on the OpenAI-compat endpoint.

This asymmetry means the Orchestrator MemoryService cannot treat all drivers uniformly. If we query Codex/Claude/Gemini with the same `memory_search(query, scope)` contract as OpenClaw, we either (a) get empty results that look like failure, or (b) fabricate responses that look real. Both are bad.

## Decision

**Three-tier classification of agent memory backends. Per-driver declaration. Cross-tier behavior defined explicitly.**

| Tier | Definition | Driver contract |
|---|---|---|
| **Real** | Agent has a queryable native memory backend. Driver forwards `memory_search` to that backend. | `search()` returns real results with actual relevance scores. |
| **Synthetic** | Agent has no native backend; driver maintains a per-agent JSONL log + external vector cache on the driver's behalf. | `search()` returns results from the synthetic cache. Quality depends on cache freshness. |
| **Empty** | Agent has no memory. Driver returns empty results + metadata flag so Orchestrator knows not to penalize. | `search()` returns `MemorySearchResult(results=[], metadata={"tier": "empty", "no_memory_backend": True})`. |

### MVP mapping (v0.1)

| Agent | Tier | Implementation |
|---|---|---|
| OpenClaw | **Real** | Per existing `openclaw_memory.py` (MVP stub returning empty + metadata; v0.2 implements WS-based `sessions_send` integration). |
| Codex | **Empty** | `CodexMemoryDriver.search()` returns empty + `tier="empty"`. No synthetic cache in MVP. |
| Anthropic (Claude) | **Empty** | `AnthropicMemoryDriver.search()` returns empty + `tier="empty"`. |
| Gemini | **Empty** | `GeminiMemoryDriver.search()` returns empty + `tier="empty"`. |

### Synthetic tier upgrade criteria (v0.2 candidate)

A driver should be promoted from Empty to Synthetic when **both**:
1. The agent is invoked frequently (e.g. > 100 invocations / day), so building a cache is worth the cost.
2. There is a viable external vector store (Qdrant / Chroma / Milvus) the Orchestrator can host.

If only one criterion holds, stay at Empty.

### Federation behavior across tiers

The Orchestrator `MemoryService.search()` (ADR-0005) fans out to all registered drivers regardless of tier. Results from `Empty` drivers arrive as `MemorySearchResult(results=[], metadata={"tier":"empty"})`. The cross-encoder rerank step down-weights empty contributions to zero (or excludes them entirely) before producing the final top-K. **Empty drivers never poison the final ranking.**

## Consequences

**Positive**
- Honest classification: callers know what to expect from each driver.
- No fabrication: Empty drivers return empty results with a clear flag, not fake data.
- Clean upgrade path: Synthetic adds value when traffic justifies it; Real stays the gold standard.
- Federation algorithm does not need per-tier special cases beyond down-weighting empty.

**Negative**
- Adds a per-driver config field (`memory_tier: Literal["real","synthetic","empty"]`) that must be set correctly; misconfiguration silently degrades.
- Cross-encoder rerank score normalization must account for tier (currently ADR-0005 normalizes within-driver; with Empty drivers having all-zero scores, the min-max step would break if not handled).
- "Empty" can be confused with "agent has no relevant memory" vs "agent has no memory backend at all"; the metadata flag disambiguates but consumers must check.

**Mitigations**
- Default tier in driver constructor = `Empty` (safe fallback; promotion is explicit).
- Driver `health_check()` returns tier alongside endpoint reachability, so misconfiguration is visible at startup.
- ADR-0005 cross-encoder explicitly excludes empty drivers from normalization (`min_max_normalize` only sees non-empty results).

## Alternatives Considered

- **A. All agents treated as Real.** Forces every driver to ship a memory backend before it can integrate. Blocks Codex / Claude / Gemini from being useful in v0.1. Rejected.
- **B. All agents treated as Empty.** Defeats the point of Memory Federation — cross-agent recall becomes impossible because no agent remembers anything. Rejected.
- **C. Three-tier classification per-driver (chosen).** Honest; clean upgrade path; federation algorithm stays simple.
- **D. Per-driver opt-in via plugin.** All drivers Real by default, opt-out to Empty. Risky — drivers ship without memory backend in MVP and would silently fabricate results. Rejected.

## Related

- ADR-0005 — Memory Federation (cross-agent fan-out + rerank). This ADR layers on top.
- `src/agentos/schemas/a2a.py` — sessionKey builder (MemoryService uses session-scoped queries).
- `src/agentos/drivers/openclaw_memory.py` — current MVP stub.
- Future: `src/agentos/drivers/{codex,anthropic,gemini}_memory.py` — Empty-tier implementations.
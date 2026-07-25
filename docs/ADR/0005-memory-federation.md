# ADR-0005: Memory Federation

- **Status**: Accepted
- **Date**: 2026-07-25
- **Deciders**: Codex, OpenClaw

## Context

Each external agent has its own memory store with different embedding models and retrieval methods. Cross-agent memory query is needed (e.g. "what did Codex say about X last week?"). Scores from different agents are not directly comparable because OpenClaw returns hybrid (vector + BM25) scores, while other vendors may return raw cosine similarity.

## Decision

**Plan B — fan-out to per-agent memory_search, min-max normalize to [0,1], then cross-encoder rerank in the Orchestrator.**

```
MemoryService.search(query, scope, top_k):
    per_agent  = fan_out({openclaw, codex, claude, gemini}_driver.memory_search(query, scope))
    candidates = min_max_normalize(per_agent, top_k=20)   # stage 1: recall signal
    reranked   = cross_encoder.rerank(query, candidates, top_k)  # stage 2: unified rank
    return reranked
```

- MVP cross-encoder: OpenAI `gpt-4o-mini` (cost negligible).
- Future: local BGE reranker for offline / cost reduction.
- Default embedding model: `text-embedding-3-small` (1536 dim).
- Driver returns hybrid weights metadata; stored in eval log for later tuning.

## Consequences

**Positive**
- No forcing of single embedding model across vendors.
- Each agent keeps its native retrieval (highest quality per agent).
- Hybrid weights logged for later tuning.
- Fan-out parallelism utilizes Concurrency Budget.

**Negative**
- Latency = max(per-agent search) + reranker call.
- Orchestrator is single point of failure for memory queries.
- Cross-encoder call adds ~100-300ms per query.

**Mitigations**
- Per-agent search runs in parallel under the Concurrency Budget.
- Cache rerank scores keyed by `(query_hash, doc_hash)`.
- Future: per-agent embedding cache; cross-encoder batched.
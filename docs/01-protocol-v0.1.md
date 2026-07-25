# AgentOS Protocol v0.1

> Frozen decision set as of 2026-07-25. Any change requires an ADR supersession.

This document is the executive summary. The detailed rationale for each
decision lives in its corresponding ADR under `docs/ADR/`.

## Frozen Decisions

### Integration (ADR-0001)
- HTTP-first; OpenAI-compatible `/v1/chat/completions` is the default integration path.
- `OpenAIDriver` handles any OpenAI-compat endpoint (OpenClaw Contract B, OpenAI, Anthropic-via-proxy, Gemini, local llama.cpp / vLLM).
- `WSDriver` wraps OpenClaw native WS gateway (Contract A) for non-chat features.

### Context Handoff (ADR-0002)
- Cross-stage context is `Artifact` (Pydantic model) + brief summary, never raw conversation history.
- Schema: `task_id`, `stage`, `producing_agent`, `artifact_type`, `files[]`, `summary`, `open_questions[]`, `next_stage_inputs`, `producer_metadata`, `schema_version`.
- `schema_version` enables evolution without breaking parsers.

### Sub-Agents (ADR-0003)
- 5 roles: Planner (flagship LLM), Judge (small model), Router (pure code), Summarizer (small model), Cost Controller (pure code).
- Default rule: code > small model > flagship model. Only Planner uses a flagship LLM in MVP.

### Evaluation (ADR-0004)
- Multi-source signals: explicit feedback, implicit signals (edit / no-edit), A/B comparison, deterministic checks (test/lint/typecheck), benchmark gold set.
- Per-stage metrics in `task_runs` table for stage-level attribution.
- Debate mode reserved for decision-class tasks only (tech choice, architecture, creative content).

### Memory Federation (ADR-0005)
- Plan B: fan-out to per-agent `memory_search` → min-max normalize scores to [0,1] → cross-encoder rerank.
- MVP cross-encoder: OpenAI `gpt-4o-mini` (cost negligible). Future: local BGE reranker.
- Default embedding: `text-embedding-3-small` (1536 dim).
- Driver returns hybrid weights metadata → stored in eval log for later tuning.
- **Boundary**: cross-agent memory sharing is the Orchestrator MemoryService's responsibility. It does NOT depend on, nor is constrained by, individual agent-internal configurations (e.g. OpenClaw `tools.sessions.visibility`).

### Concurrency & Streaming (ADR-0006)
- Concurrency Budget = 4 (configurable per host).
- Streaming counts as 1 slot until round ends (`asyncio.Semaphore(N)` wrap).
- Parallel dispatch saves wall-clock latency but is bounded by the budget.

### Driver Failure Policy (ADR-0007)
- Default: fail-fast (raise `DriverError` immediately).
- Configurable retry: default 3 attempts, exponential backoff 1s/2s/4s + jitter.
- Cross-driver fallback chain DISABLED by default; must be explicitly listed per task.
- Fallback decisions belong in the task-planning layer, not the driver layer.

### Artifact Storage (ADR-0008)
- Local filesystem MVP. Layout: `G:/AgentOS/artifacts/{task_id}/{stage_id}/{artifact_id}.json` + `files/` subdir.
- Config: `max_size_mb=50` per artifact, `cleanup_after_days=30`.
- Future: S3 / MinIO via the same `ArtifactStore` interface.

## Session Key Convention

```
task:<task_id>:stage:<stage_id>[:sub:<sub_id>]
```

- Max length 128 chars.
- Avoid reserved prefixes: `subagent:`, `cron:`, `acp:` (reserved by OpenClaw).
- Agent routing is NOT encoded in sessionKey; drivers receive it via separate config / header.

## A2A Message Types

`TASK_REQUEST`, `TASK_ACCEPT`, `TASK_PROGRESS`, `TASK_BLOCKED`,
`KNOWLEDGE_SHARE`, `REVIEW_REQUEST`, `DECISION`, `HANDOFF`

Schema: `id`, `from_agent`, `to_agent`, `type`, `priority`, `payload`,
`created_at`.

## Process

- **ADR changes** (`docs/ADR/*.md`, this protocol doc) require a PR with review by at least one other decider. Direct commits to `main` are not allowed for these paths. The ADR PR template lives at the bottom of [`docs/ADR/README.md`](ADR/README.md).
- **Driver / test code** (`src/agentos/**`, `tests/**`) commits directly to `main` are OK. Commit message MUST reference the relevant ADR number (e.g. `feat(memory): fan-out search per ADR-0005`).
- **Operational docs** (`docs/02-bootstrap.md`) commits directly to `main` are OK.

## What v0.1 does NOT cover

- Multi-host worker pool (single-host only)
- Distributed Memory (cross-host cross-agent)
- Tool-schema registry (hard tool whitelist)
- Streaming chunk protocol over A2A bus
- Auto-scaling Concurrency Budget
- Cross-driver cost attribution
- Native `memory.search` RPC negotiation with vendors

These are queued for v0.2.
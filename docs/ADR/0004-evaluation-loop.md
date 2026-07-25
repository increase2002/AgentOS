# ADR-0004: Evaluation Loop

- **Status**: Accepted
- **Date**: 2026-07-25
- **Deciders**: Codex, OpenClaw

## Context

To improve agent selection over time, AgentOS needs multi-source quality signals. A single source (e.g. user thumbs-up) is too noisy and sparse to drive decisions.

## Decision

**Five signal sources, weighted sum feeds `agent_metrics`. Per-stage metrics in `task_runs`. Debate mode reserved for decision-class tasks.**

| Signal | Strength | Volume | Notes |
|---|---|---|---|
| Explicit user feedback (thumbs) | Strong | Low | Users rarely bother |
| Implicit (no-edit / edit / regenerate) | Strong | High | Cheap to capture |
| A/B comparison across agents | Medium | Medium | Same task, two drivers |
| Deterministic (test / lint / typecheck) | Strong | High | Code tasks only |
| Benchmark gold set | Medium | Medium | Stable but synthetic |

Per-stage metrics (not end-to-end) — 80% of multi-agent bugs hide inside a single stage, and end-to-end alone cannot localize them.

**Debate mode** (multiple agents propose, Judge picks) is enabled only for decision-class tasks:
- Tech-stack choice / architecture decision
- Creative content (copy, design direction)
- Architecture-level ambiguity

Disabled for: code, test, deploy, retrieval, data analysis (ground truth exists; debate is wasteful).

## Consequences

**Positive**
- Multi-source signals compensate for individual weakness.
- Per-stage attribution enables targeted debugging.
- Debate limited to high-value tasks keeps cost in check (3-5x single-agent cost).

**Negative**
- Eval pipeline is complex (5 sources + per-stage + debate gating).
- Implicit signal heuristics need tuning per task type.
- Benchmark drift over time as the field evolves.

**Mitigations**
- Implicit signal definitions documented in `docs/04-eval-signals.md` (planned).
- Benchmark suite versioned; CI alerts on drift > 5%.
- Debate eligibility check is at planning time, not runtime, so cost is bounded.
# ADR-0007: Driver Failure Policy

- **Status**: Accepted
- **Date**: 2026-07-25
- **Deciders**: Codex, OpenClaw (龙大), Increase (老大)

## Context

External agents fail (timeouts, rate limits, output quality, model degradation). The Orchestrator failure-handling strategy directly affects cost and quality attribution.

## Decision

**Default fail-fast + configurable retry. Cross-driver fallback chain DISABLED by default; must be explicitly opted into per task.**

| Behavior | Default | Configurable |
|---|---|---|
| Raise `DriverError` on failure (fail-fast) | Yes | `fail_fast: bool = True` |
| Same-driver retry (exp. backoff + jitter) | Yes (3 attempts, 1s/2s/4s) | `retry.max_attempts`, `retry.base_delay_s`, `retry.jitter` |
| Cross-driver fallback chain | Disabled | `fallback_drivers: [name, ...]` required |

Fallback is left to the **task-planning layer** (Planner decides "if Codex fails, try Claude") rather than the driver layer — keeps cost predictable and quality attribution clean.

## Consequences

**Positive**
- Predictable cost; no silent 3x token burn via fallback chains.
- Quality attribution preserved (eval knows exactly which driver ran).
- User contract clear: fail-fast surfaces issues immediately.

**Negative**
- Transient failures reach user as errors (vs silent recovery).
- Retry storms possible if budget is set too high.

**Mitigations**
- Planner can pre-declare fallback at task level if cost is acceptable.
- Cost Controller enforces retry budget cap per task.

## Alternatives Considered

- **A. Fail-fast only, no retry.** Surfaces transient errors to user unnecessarily (rate limits, brief outages). Rejected.
- **B. Fail-fast + configurable same-driver retry (chosen).** Graceful on transient failures; cost bounded by retry config; quality attribution preserved.
- **C. Auto-fallback chain enabled by default.** Cost unpredictable (could burn 3x tokens silently); eval attribution breaks (which driver actually ran?). Rejected as default.
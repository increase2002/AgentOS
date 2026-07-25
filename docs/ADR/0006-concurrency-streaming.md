# ADR-0006: Concurrency & Streaming

- **Status**: Accepted
- **Date**: 2026-07-25
- **Deciders**: Codex, OpenClaw (龙大), Increase (老大)

## Context

Multiple stages may run in parallel. Long-running streams consume connections. Without a budget, parallel dispatch can saturate the host and starve short requests.

## Decision

**Concurrency Budget = 4 by default (configurable per host). Streaming counts as 1 slot until the round ends — not per chunk.**

Enforced with `asyncio.Semaphore(N)` wrapping every `driver.chat()` call. Parallel dispatch (e.g. 3 sub-tasks in `Parallel` mode) shares the budget.

## Consequences

**Positive**
- Predictable resource usage per host.
- Long streams do not starve short requests.
- Static budget is easy to reason about.

**Negative**
- Static budget does not auto-scale to host capability.
- Over-budget tasks queue up; latency spikes under load.

**Mitigations**
- Per-host budget config in `agentos.yaml`; tune per deployment.
- Future: load-aware dynamic budget (CPU/memory signals).

## Alternatives Considered

- **A. No budget.** DoS risk; resource starvation; one runaway task can wedge the host. Rejected.
- **B. Static budget, streaming counts as 1 slot (chosen).** Predictable; streaming semantics correct (one round = one slot).
- **C. Dynamic load-aware budget (deferred).** Better fit but harder to reason about; deferred to v0.2.
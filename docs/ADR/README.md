# Architecture Decision Records

This directory contains the Architecture Decision Records (ADRs) for AgentOS.
Each ADR captures one significant architectural decision: the context, the
choice, the consequences, and the alternatives that were considered.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-integration-method.md) | Integration Method (HTTP + OpenAI-compat) | Accepted |
| [0002](0002-context-handoff.md) | Context Handoff (Artifact + summary) | Accepted |
| [0003](0003-internal-sub-agents.md) | Internal Sub-Agents (5 roles) | Accepted |
| [0004](0004-evaluation-loop.md) | Evaluation Loop (multi-source signals) | Accepted |
| [0005](0005-memory-federation.md) | Memory Federation (fan-out + rerank) | Accepted |
| [0006](0006-concurrency-streaming.md) | Concurrency & Streaming | Accepted |
| [0007](0007-driver-failure-policy.md) | Driver Failure Policy (fail-fast + retry) | Accepted |
| [0008](0008-artifact-storage.md) | Artifact Storage (local FS) | Accepted |

For the frozen decision summary, see [`docs/01-protocol-v0.1.md`](../01-protocol-v0.1.md).
For the original design document, see [`docs/AgentOS_Multi_Agent_Architecture_Design.md`](../AgentOS_Multi_Agent_Architecture_Design.md).

## ADR Template

All ADRs in this directory use the following template:

```markdown
# ADR-NNNN: <title>

- **Status**: Proposed | Accepted | Deprecated
- **Date**: YYYY-MM-DD
- **Deciders**: Codex, OpenClaw (龙大), Increase (老大)

## Context
<what problem we faced>

## Decision
<what we chose>

## Consequences
<trade-offs of the choice>

## Alternatives Considered
<A / B / C with reasoning>
```

## ADR PR Template

When proposing a new ADR or superseding an existing one, open a PR with this body:

```markdown
### ADR
- ADR number: NNNN
- Status change: Proposed → Accepted | Accepted → Deprecated
- Deciders: ...

### Context
<why this ADR now>

### Decision
<what we decided>

### Consequences
<positive + negative + mitigations>

### Alternatives Considered
<A / B / C with reasoning>
```

PRs touching `docs/ADR/*.md` or `docs/01-protocol-v0.1.md` require review from at least one other decider before merge. Direct commits to `main` are not allowed for these paths.
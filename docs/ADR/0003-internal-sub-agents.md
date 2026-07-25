# ADR-0003: Internal Sub-Agents

- **Status**: Accepted
- **Date**: 2026-07-25
- **Deciders**: Codex

## Context

Some orchestrator functions (planning, routing, judging, summarizing, cost-tracking) can be done by LLMs or by code. The choice affects cost, predictability, and reliability.

## Decision

**Five sub-agent roles with explicit model assignment. Default rule: code > small model > flagship model.**

| Role | Implementation | Model |
|---|---|---|
| Planner (DAG generation) | LLM | Flagship |
| Judge (rubric scoring) | LLM | Small model |
| Router (agent matching) | Pure code | None |
| Summarizer (artifact distillation) | LLM | Small model |
| Cost Controller (budget tracking) | Pure code | None |

Only Planner uses a flagship LLM in MVP. Code-based roles have zero token cost and deterministic behavior.

## Consequences

**Positive**
- Predictable cost; only Planner burns flagship-model tokens.
- Code-based roles (Router, Cost Controller) are deterministic and unit-testable.
- Clear contracts per role.

**Negative**
- Hybrid code+LLM architecture is more complex than pure-LLM.
- Planner quality caps the whole orchestrator (single point of failure for DAG quality).

**Mitigations**
- Router logic is fully testable; can be tuned without retraining.
- Cost Controller has hard budget caps (over-budget = cancel + escalate).
- Planner prompts are versioned; regressions caught by eval suite (ADR-0004).
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
| [0009](0009-tool-subset-enforcement.md) | Tool Subset Enforcement (plan-only / read-only) | Accepted (MVP), Proposed (v0.2 hard) |
| 0010 | Orchestrator Engine (Core API + DAG Execution) | Proposed (OpenClaw, this week) |
| [0011](0011-memory-backend-tiering.md) | Memory Backend Tiering (Real / Synthetic / Empty) | Accepted |

For the frozen decision summary, see [`docs/01-protocol-v0.1.md`](../01-protocol-v0.1.md).
For the original design document, see [`docs/AgentOS_Multi_Agent_Architecture_Design.md`](../AgentOS_Multi_Agent_Architecture_Design.md).
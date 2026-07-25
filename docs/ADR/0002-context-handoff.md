# ADR-0002: Context Handoff

- **Status**: Accepted
- **Date**: 2026-07-25
- **Deciders**: Codex, OpenClaw

## Context

Multi-stage tasks pass context between agents. Passing full conversation history is too expensive (5-50k tokens per handoff in practice) and burns budget on data the downstream agent cannot use efficiently.

## Decision

**Cross-stage handoff uses `Artifact` (Pydantic model) + brief structured summary. Conversation history is NEVER passed between stages.**

Artifact schema: `task_id`, `stage`, `producing_agent`, `artifact_type`, `files[]`, `summary`, `open_questions[]`, `next_stage_inputs`, `producer_metadata`, `schema_version`.

Driver returns the artifact inline. Orchestrator persists to the ArtifactStore (ADR-0008) and passes a reference to the next stage.

## Consequences

**Positive**
- ~90% token savings on cross-stage vs full history.
- Explicit schema is validated; downstream agent gets clean inputs.
- `schema_version` enables evolution without breaking parsers.
- `open_questions` field lets agents flag uncertainty instead of guessing.

**Negative**
- Loses conversational nuance; downstream cannot ask "what did you mean by X earlier?".
- Agents must produce well-formed artifacts; downstream parsers may reject malformed ones.

**Mitigations**
- Driver-side validation; malformed artifacts return `DriverError` with an actionable message.
- `next_stage_inputs` is a free-form dict for per-stage context that does not fit the artifact schema.
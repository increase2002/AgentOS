# ADR-0008: Artifact Storage

- **Status**: Accepted
- **Date**: 2026-07-25
- **Deciders**: Codex, OpenClaw

## Context

Artifacts (files, structured data) need a storage layer for cross-stage reference and post-hoc evaluation. Where do they live?

## Decision

**Local filesystem MVP. Layout: `G:/AgentOS/artifacts/{task_id}/{stage_id}/{artifact_id}.json` plus `files/` subdirectory.**

- Per-artifact size cap: `max_size_mb=50`.
- Auto-cleanup: `cleanup_after_days=30`.
- Driver returns `Artifact` + inline small files (<= 64KB). Large files written by Orchestrator, path back-filled in artifact metadata.
- Future: S3 / MinIO swap via the same `ArtifactStore` interface.

## Consequences

**Positive**
- Zero infra dependency for MVP.
- Direct file access for eval pipeline (no API round-trip).
- Single-host is enough for development.

**Negative**
- Single-host only; no cross-host sharing.
- No built-in replication; disk loss = artifact loss.

**Mitigations**
- `ArtifactStore` interface designed for S3/MinIO swap.
- Eval pipeline reads via the interface, not raw FS.
- Out of scope for v0.1: replication, CDN, signed URLs.
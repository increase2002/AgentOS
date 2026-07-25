# ADR-0008: Artifact Storage

- **Status**: Accepted
- **Date**: 2026-07-25
- **Deciders**: Codex, OpenClaw (龙大), Increase (老大)

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

## Alternatives Considered

- **A. Agent workspace (each agent stores its own artifacts).** Fragmented; cross-stage references must walk through every agent. Rejected.
- **B. Git LFS.** Version-controlled, but slow for large files and awkward diff. Rejected.
- **C. Orchestrator local FS MVP, S3/MinIO later via interface (chosen).** Clean cross-stage references, zero infra for MVP, swappable interface for v0.2+ scale-out.
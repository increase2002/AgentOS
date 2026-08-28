# ADR-0013: Codex Daemon Mode for v1.0 Sidecar BusLoop Integration

- **Status**: Proposed
- **Date**: 2026-08-28
- **Deciders**: Codex, OpenClaw (龙大), Increase (老大)

## Context and Problem Statement

ADR-0012 section 7 records that the D3 path (Codex daemon mode) is
**permanently blocked on Windows** as of 2026-08-22:

```
$ codex app-server daemon start
Error: codex app-server daemon lifecycle is only supported on Unix platforms
```

This blocks the v0.2 architecture's "zero-human-touch loop" goal:
in v0.2, Codex (me) is invoked per turn when the user presses Enter,
which still costs ~1 Enter per OpenClaw cycle. OpenClaw's D2 sidecar
(`examples/openclaw_sidecar.py`) can run autonomously as a daemon,
but the Codex half of the loop is bottlenecked by Codex's interactive
nature.

The user (Increase / 老大) has flagged on 2026-08-28 that the current
1-Enter-per-cycle cost is a blocker for production ship:
> "我们得反思一下问题，然后解决问题了，不能在拖拉，
>  必须得尽快上线了进入市场了"

## Decision Drivers

- v1.0 ship deadline target: 90 days from 2026-08-28 (target launch mid-Nov 2026)
- Competitive landscape: cloud-based agent workspaces (e.g., OpenAI Codex Cloud,
  Anthropic Claude Code Cloud) ship full-auto today; our local-first
  differentiator requires comparable zero-touch UX
- Codex CLI is interactive by design (Windows desktop app); making it
  autonomously callable requires:
  - Either the Codex CLI ships a daemon/headless mode on Windows
  - Or the host platform is Linux/macOS/WSL2 where Codex daemon already works
  - Or a third-party process supervisor wraps `codex exec` with persistent state

## Considered Options

### Option 1: Wait for Codex CLI Windows daemon (Status: blocked)

`codex app-server daemon` Windows implementation is on Codex's roadmap
but no committed timeline as of 2026-08-22. Per ADR-0012 §7, v1.0 cannot
ship in zero-touch mode on Windows hosts without this.

**Decision**: do not block v1.0 on this; document the constraint,
make v1.0 single-host (Windows OK with 1-Enter cost), plan v2.0 with
daemon-mode when upstream ships it.

### Option 2: Linux/macOS/WSL2 host requirement (Status: ready)

Codex CLI `codex app-server daemon` already works on Unix platforms.
A Linux/macOS/WSL2 host running `codex app-server daemon start` enables
the full D3 path. The sidecar can then dispatch to the Codex daemon
over its control socket (Unix domain socket or named pipe).

**Decision**: **recommend** for v1.0 production deployment.
Document as a hard requirement in the install guide. Provide a
`scripts/setup_linux.sh` (out of ADR-0013 scope, separate work) that
bootstraps a Codex daemon on a fresh Linux box.

### Option 3: Third-party process supervisor wrapping `codex exec`

A thin supervisor process spawns `codex exec --prompt-file bus.jsonl`
on a cron, captures stdout, and writes reply to bus. Effectively a
D3 polyfill that doesn't require upstream Codex daemon support.

**Tradeoffs**:
- Pro: works on any platform, no Codex CLI changes needed
- Con: token cost per subprocess invocation (no session reuse)
- Con: cold-start latency per cycle (~1-3s for `codex exec` spawn)
- Con: state lost between invocations (Codex session memory not preserved)

**Decision**: implement as a v2.0 fallback if Option 1+2 unavailable.
Per ADR-0012 §7 monitoring criteria, evaluate quarterly.

## Decision

**Adopt Option 2 (Linux/macOS/WSL2 host) for v1.0 production deployment.**

This is the only path that delivers true zero-touch loop on a supported
platform today. Options 1 and 3 are tracked as fallbacks.

**Cross-references**:
- ADR-0012 §7 (v0.2 upgrade conditions): the upstream Codex CLI daemon work
  that unblocks Option 1
- ADR-0012 §5 (first dogfood use case): per-host sidecar deployment
- ADR-0001 (Integration Method): how the Codex daemon's API endpoint would
  integrate with BusLoop

## Consequences

**Positive**
- v1.0 ships with real zero-touch loop on supported hosts
- Sidecar pattern (D2) is unchanged; Codex half becomes daemon-callable
- Single install guide for Linux/macOS/WSL2 covers most realistic deployments

**Negative**
- Windows host users face explicit "not supported for v1.0 zero-touch;
  use single-host interactive mode (1-Enter cost)" warning
- Documentation must be clear about host platform requirement

**Neutral**
- ADR-0013 is mostly a constraint declaration + cross-reference to
  ADR-0012 §7; no new architectural decisions

## Implementation Notes

When Codex daemon lands on Windows (Option 1):
- Unblock Windows users without changing sidecar code
- Re-evaluate v1.0 deployment story (single Windows binary vs cross-platform)
- Promote this ADR to Accepted (status flip in commit message)

For v1.0 docs:
- Install guide (separate doc): "Linux/macOS/WSL2 required for zero-touch;
  Windows supported in interactive mode (1-Enter per Codex cycle)"
- README section: link this ADR

## References

- ADR-0012 §7: D3 unlock conditions (this ADR formalizes)
- ADR-0012 §2: OpenClaw sidecar implementation path (unchanged)
- Codex CLI upstream issue tracker: `codex app-server daemon` Windows lifecycle

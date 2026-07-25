# ADR-0009: Tool Subset Enforcement (Plan-Only / Read-Only Mode)

- **Status**: Accepted (MVP soft), Proposed (v0.2 hard)
- **Date**: 2026-07-25
- **Deciders**: Codex, OpenClaw (龙大), Increase (老大)

## Context

`BaseDriver.chat()` advertises a `tool_subset` parameter that lets the Orchestrator restrict which tools a downstream agent may invoke during a single chat round. The two primary use cases are:

1. **Plan-only mode** (`tool_subset=[]`) — invoke an agent to draft a plan / strategy without giving it write or shell access.
2. **Read-only sandboxing** (`tool_subset=["read_file", "grep", "list_dir"]`) — give the agent only safe tools; deny `write_file`, `edit`, `shell_exec`, etc.

This is a **safety boundary**, not a quality-of-life feature. A misbehaving agent given full tool access can do destructive things (delete files, push to remote, exfiltrate secrets). The `tool_subset` contract is the only mechanism the Orchestrator has to scope that risk before a chat round starts.

The contract is currently exposed by `BaseDriver` and implemented by `OpenAIDriver` (and inherited by `OpenClawDriver`), but the implementation choice between *soft* and *hard* enforcement has not been documented as an ADR.

## Decision

**MVP (now, accepted): soft constraint via system-prompt injection.**

`OpenAIDriver._build_messages()` prepends a `system` role message when `tool_subset` is non-None. Three modes:

| `tool_subset` value | System message content |
|---|---|
| `None` | (no constraint — default) |
| `[]` | "You are in plan-only / read-only mode. Do not invoke any tools, do not write any files. Analyze the request and return a structured plan only." |
| `["read_file", "grep"]` | "You may only use these tools: read_file, grep. If a request requires a tool not in this list, refuse and explain why." |

The choice is recorded in `ChatResult.metadata["tool_subset"]` for eval attribution.

**v0.2 (proposed): hard whitelist via `tools=` parameter.**

When the agent SDK supports filtering by tool name (OpenAI `tools=[...]`, Anthropic `tools=[...]`, Gemini `tools=[...]`), the driver passes ONLY the listed tool schemas to the API. The agent physically cannot invoke excluded tools.

Hard enforcement requires:
- A per-agent `tool_registry: dict[str, dict]` config — mapping tool name to JSON schema.
- A driver helper `_filter_tools(subset, registry) -> list[dict]` that intersects subset with registry.
- Vendor cooperation: each agent vendor must expose its full tool schema catalog.

## Consequences

**Positive (MVP)**
- Zero new infrastructure; works today for any LLM that follows system prompts.
- Cross-vendor (works for OpenAI, Anthropic, Gemini, local models with same prompt).
- Already implemented in `OpenAIDriver._build_messages()` (commit `e3df598`) and covered by 4 unit tests.

**Negative (MVP)**
- **Weak**: a sufficiently motivated agent can ignore the system prompt. There is no API-level guarantee.
- Requires eval-time monitoring (signal: agent invoked a non-listed tool) and user-feedback reporting.
- Eval loop must explicitly catch violations; otherwise the safety contract silently degrades.

**Positive (v0.2)**
- **Strong**: agent cannot invoke excluded tools — server-side refusal.
- Eval can directly observe which tools the agent invoked vs. was allowed.

**Negative (v0.2)**
- Requires per-agent tool schema registry; vendor cooperation needed.
- Inconsistent across vendors: each agent exposes tools differently.
- Hard constraint may break agents that internally require specific tools (e.g. a code agent that needs `shell_exec` always).

**Mitigations**
- MVP: log every tool invocation (`tool_calls` field in response), compare against `tool_subset`; emit warning on mismatch; surface in eval dashboard.
- MVP: periodic red-team eval — try to make the agent break the constraint; track violation rate.
- v0.2: per-agent `tool_registry` is opt-in; default to MVP soft if not configured.
- v0.2: when a stage requires always-on tools (e.g. shell), document them as `required_tools` outside the subset scope.

## Alternatives Considered

- **A. No constraint at all.** Pass-through to the agent with full tool access. Simple but unsafe — a single bad agent round can corrupt the host. Rejected.
- **B. Server-side RBAC via OS sandbox.** Run each agent in a restricted OS environment (chroot, seccomp, container) with hard file / network policy. Strong but operationally heavy for MVP; deferred to a follow-up ADR (`ADR-0011-os-sandbox` candidate).
- **C. Soft constraint MVP + hard whitelist v0.2 (chosen).** Ships today with documented weakness; converges to hard enforcement as vendor SDKs mature. Best balance of safety and engineering cost.
- **D. Hard whitelist only (skip MVP).** Requires tool schema registry before any release. Blocks MVP. Rejected.

## Related

- Implementation: `src/agentos/drivers/openai_driver.py::_build_messages` (MVP, soft).
- Tests: `tests/test_drivers.py::test_build_messages_*` (4 cases).
- Follow-up: ADR-0007 (Driver Failure Policy) is orthogonal — that ADR covers retry / fail-fast, not tool restriction.
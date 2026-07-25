# ADR-0001: Integration Method

- **Status**: Accepted
- **Date**: 2026-07-25
- **Deciders**: Codex, OpenClaw

## Context

External agents (OpenClaw, Codex, Claude, Gemini, ...) each expose their own native protocol. AgentOS needs a unified internal driver interface, but should not reimplement what each vendor already provides well.

## Decision

**Hybrid: HTTP-first with OpenAI-compatible chat as the default; WebSocket for native node capabilities.**

1. **`OpenAIDriver`** is the default integration path for any agent exposing `/v1/chat/completions`. This includes:
   - OpenClaw Contract B (must be explicitly enabled in `openclaw.json`)
   - OpenAI / Azure OpenAI
   - Anthropic via OpenAI-compat proxy
   - Google Gemini via official OpenAI-compat layer
   - Local llama.cpp / vLLM servers

2. **`WSDriver`** wraps the OpenClaw native WebSocket gateway (Contract A, port 18789) for capabilities beyond chat: camera, screen, voice, node management, cron, `sessions_send`.

3. **Per-agent driver subclasses** (`OpenClawDriver`, future `CodexDriver`, `ClaudeDriver`, `GeminiDriver`) layer agent-specific behavior on top of `OpenAIDriver`: session-key header conventions, attachment encoding, error-code translation.

4. Only one third-party HTTP client in MVP: the official OpenAI Python SDK. No per-vendor SDKs.

## Consequences

**Positive**
- One driver class covers ~80% of use cases; zero-cost integration for any OpenAI-compat endpoint.
- Standard SDK, well-documented, type-safe.
- Switching agents is a config change, not a code change.

**Negative**
- Forces the OpenAI API surface; some vendor-native features cannot be expressed (e.g. Anthropic prompt caching, Gemini function-calling with grounding).
- OpenClaw Contract B requires explicit enable + a valid gateway token; operationally heavier than a no-config integration.
- Future vendor-specific optimizations require per-agent subclass work.

**Mitigations**
- Per-agent subclass pattern isolates vendor-specific code.
- Hard tool-whitelist enforcement deferred (ADR-0007 follow-up).
- Attachment file-API path (binary, large files) added in next iteration via per-driver `should_use_file_api()` hook.
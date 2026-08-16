"""Token resolution helper for OpenClaw gateway.

OpenClaw stores its gateway auth token in two possible places:
  1. Canonical: ``~/.openclaw/openclaw.json`` -> ``gateway.auth.token`` (JSON5)
  2. Dev mode: ``G:\\AgentOS\\.openclaw\\gateway.token`` (plain text, gitignored)

Per OpenClaw reviewer (2026-08-16), the canonical source is #1; #2 is a
workaround that Codex originally wrote without knowing the canonical
path. Drivers should call :func:`resolve_openclaw_token` instead of
hardcoding either path so the source of truth is the OpenClaw-managed
config file.

Both paths are injectable for testing: pass ``canonical_path`` or
``dev_fallback_path`` to override the defaults.
"""

from __future__ import annotations

from pathlib import Path

from agentos.drivers.openclaw_config import load_openclaw_config


class OpenClawTokenError(RuntimeError):
    """Raised when no OpenClaw token can be resolved from any known path."""


DEFAULT_CANONICAL_PATH = Path.home() / ".openclaw" / "openclaw.json"
DEFAULT_DEV_FALLBACK_PATH = Path(r"G:\\AgentOS\\.openclaw\\gateway.token")


def resolve_openclaw_token(
    *,
    canonical_path: Path | None = None,
    dev_fallback_path: Path | None = None,
) -> str:
    """Return the OpenClaw gateway auth token.

    Resolution order:
      1. Canonical: ``~/.openclaw/openclaw.json`` -> ``gateway.auth.token``
      2. Dev fallback: ``G:\\AgentOS\\.openclaw\\gateway.token``

    Args:
        canonical_path: Override the canonical config path (for tests).
        dev_fallback_path: Override the dev fallback file path (for tests).

    Raises:
        OpenClawTokenError: if no token is found at either path.
    """
    canonical = canonical_path or DEFAULT_CANONICAL_PATH
    if canonical.exists():
        try:
            cfg = load_openclaw_config(canonical)
            if cfg.auth_token:
                return cfg.auth_token
        except Exception:
            pass

    dev = dev_fallback_path or DEFAULT_DEV_FALLBACK_PATH
    if dev.exists():
        return dev.read_text(encoding="utf-8").strip()

    raise OpenClawTokenError(
        f"No OpenClaw token found at {canonical} (gateway.auth.token) "
        f"or {dev}. Configure OpenClaw first."
    )

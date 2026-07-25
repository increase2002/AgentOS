"""A2A protocol constants and sessionKey helpers."""

from __future__ import annotations

# Reserved sessionKey prefixes that MUST NOT be used by AgentOS
# (these belong to OpenClaw internal subagent/cron/acp subsystems).
RESERVED_SESSION_PREFIXES: tuple[str, ...] = (
    "subagent:",
    "cron:",
    "acp:",
)

# AgentOS sessionKey convention
SESSION_KEY_TEMPLATE = "task:{task_id}:stage:{stage_id}"
SESSION_KEY_SUB_TEMPLATE = "task:{task_id}:stage:{stage_id}:sub:{sub_id}"
SESSION_KEY_MAX_LENGTH = 128


def build_session_key(task_id: str, stage_id: str, sub_id: str | None = None) -> str:
    """Build an AgentOS sessionKey.

    Raises ValueError if the resulting key exceeds SESSION_KEY_MAX_LENGTH or
    uses a reserved prefix.
    """
    if sub_id:
        key = SESSION_KEY_SUB_TEMPLATE.format(
            task_id=task_id, stage_id=stage_id, sub_id=sub_id
        )
    else:
        key = SESSION_KEY_TEMPLATE.format(task_id=task_id, stage_id=stage_id)

    if len(key) > SESSION_KEY_MAX_LENGTH:
        raise ValueError(
            f"sessionKey too long ({len(key)} > {SESSION_KEY_MAX_LENGTH}): {key}"
        )
    if any(key.startswith(prefix) for prefix in RESERVED_SESSION_PREFIXES):
        raise ValueError(f"sessionKey uses reserved prefix: {key}")
    return key
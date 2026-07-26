"""Session-key builder + validator for Orchestrator Engine.

Session keys namespace stages and sub-tasks so messages + telemetry can
be correlated across multiple drivers and replays.

Format
------

* Stage key:    ``task:{task_id}:stage:{stage_id}``
* Sub-task key: ``task:{task_id}:stage:{stage_id}:sub:{sub_id}``

Constraints (per schemas/message.py + ADR-0010)
-----------------------------------------------

* Length ≤ 128 characters.
* Must not start with reserved prefixes: ``subagent:``, ``cron:``,
  ``acp:`` (used by OpenClaw core for its own internal sessions).
* ``task_id`` / ``stage_id`` / ``sub_id`` must be non-empty and contain
  only ``[a-zA-Z0-9_-]`` characters (filesystem + log-line safe).

Used by
-------

* ``Engine._dispatch_stage`` to build the key passed to ``driver.chat``.
* ``JSONLHook.record`` to tag telemetry events with the originating stage.
* Bus messages (via ``payload["session_key"]``) so external observers can
  correlate events.
"""

from __future__ import annotations

import re
from typing import Final

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

MAX_SESSION_KEY_LEN: Final[int] = 128

RESERVED_PREFIXES: Final[tuple[str, ...]] = ("subagent:", "cron:", "acp:")

_SAFE_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]+$")


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class InvalidSessionKeyError(ValueError):
    """Raised when a session key would violate namespace or length rules."""


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #


def build_stage_key(task_id: str, stage_id: str) -> str:
    """Build the session key for a top-level stage.

    >>> build_stage_key("t-001", "research")
    'task:t-001:stage:research'
    """
    _check_id("task_id", task_id)
    _check_id("stage_id", stage_id)
    key = f"task:{task_id}:stage:{stage_id}"
    return _finalize(key)


def build_subtask_key(task_id: str, stage_id: str, sub_id: str) -> str:
    """Build the session key for a sub-task within a stage.

    >>> build_subtask_key("t-001", "research", "web-search")
    'task:t-001:stage:research:sub:web-search'
    """
    _check_id("task_id", task_id)
    _check_id("stage_id", stage_id)
    _check_id("sub_id", sub_id)
    key = f"task:{task_id}:stage:{stage_id}:sub:{sub_id}"
    return _finalize(key)


# --------------------------------------------------------------------------- #
# Validate
# --------------------------------------------------------------------------- #


def validate_session_key(key: str) -> None:
    """Raise ``InvalidSessionKeyError`` if ``key`` is not a valid session key.

    Use this to validate user-supplied or externally-supplied keys (e.g.
    when the CLI passes a session_key override).
    """
    if not key:
        raise InvalidSessionKeyError("session key is empty")
    if len(key) > MAX_SESSION_KEY_LEN:
        raise InvalidSessionKeyError(
            f"session key too long: {len(key)} > {MAX_SESSION_KEY_LEN}"
        )
    if any(key.startswith(p) for p in RESERVED_PREFIXES):
        raise InvalidSessionKeyError(
            f"session key uses reserved prefix; "
            f"reserved={RESERVED_PREFIXES}"
        )
    if not key.startswith("task:"):
        raise InvalidSessionKeyError(
            f"session key must start with 'task:'; got {key!r}"
        )
    parsed = parse_session_key(key)
    if not parsed.get("task_id") or not parsed.get("stage_id"):
        raise InvalidSessionKeyError(
            f"session key has empty task_id / stage_id: {key!r}"
        )


# --------------------------------------------------------------------------- #
# Parse
# --------------------------------------------------------------------------- #


def parse_session_key(key: str) -> dict[str, str]:
    """Parse a session key into its components.

    Returns ``{}`` for keys that do not match the expected format.

    >>> parse_session_key("task:t-001:stage:research")
    {'task_id': 't-001', 'stage_id': 'research'}
    >>> parse_session_key("task:t-001:stage:research:sub:web-search")
    {'task_id': 't-001', 'stage_id': 'research', 'sub_id': 'web-search'}
    """
    out: dict[str, str] = {}
    parts = key.split(":")
    if len(parts) >= 4 and parts[0] == "task" and parts[2] == "stage":
        out["task_id"] = parts[1]
        out["stage_id"] = parts[3]
    if len(parts) >= 6 and parts[4] == "sub":
        out["sub_id"] = parts[5]
    return out


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _check_id(name: str, value: str) -> None:
    if not value:
        raise InvalidSessionKeyError(f"{name} is empty")
    if not _SAFE_ID_RE.match(value):
        raise InvalidSessionKeyError(
            f"{name}={value!r} contains invalid characters; "
            f"allowed: [A-Za-z0-9_-]"
        )


def _finalize(key: str) -> str:
    validate_session_key(key)
    return key
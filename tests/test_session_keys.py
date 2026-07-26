"""Tests for session_keys module."""

from __future__ import annotations

import pytest

from agentos.orchestrator.session_keys import (
    InvalidSessionKeyError,
    build_stage_key,
    build_subtask_key,
    parse_session_key,
    validate_session_key,
)


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #


def test_build_stage_key():
    assert build_stage_key("t-001", "research") == "task:t-001:stage:research"


def test_build_subtask_key():
    assert (
        build_subtask_key("t-001", "research", "web-search")
        == "task:t-001:stage:research:sub:web-search"
    )


@pytest.mark.parametrize("name", ["task_id", "stage_id", "sub_id"])
def test_build_rejects_empty(name):
    with pytest.raises(InvalidSessionKeyError):
        build_stage_key("", "x") if name == "task_id" else build_subtask_key("x", "y", "")


@pytest.mark.parametrize(
    "bad_value",
    ["a b", "a/b", "a:b", "a.b", "a$b", "中文", "toolong" + "x" * 200],
)
def test_build_rejects_invalid_chars(bad_value):
    with pytest.raises(InvalidSessionKeyError):
        build_stage_key(bad_value, "y")


# --------------------------------------------------------------------------- #
# Validate
# --------------------------------------------------------------------------- #


def test_validate_accepts_built_keys():
    validate_session_key("task:t-001:stage:research")
    validate_session_key("task:t-001:stage:research:sub:web-search")


@pytest.mark.parametrize(
    "bad_key",
    [
        "",
        "x" * 200,  # too long
        "subagent:foo",  # reserved
        "cron:bar",  # reserved
        "acp:baz",  # reserved
        "not-a-task-key",
        "task::empty",
    ],
)
def test_validate_rejects_bad_keys(bad_key):
    with pytest.raises(InvalidSessionKeyError):
        validate_session_key(bad_key)


# --------------------------------------------------------------------------- #
# Parse
# --------------------------------------------------------------------------- #


def test_parse_stage_key():
    out = parse_session_key("task:t-001:stage:research")
    assert out == {"task_id": "t-001", "stage_id": "research"}


def test_parse_subtask_key():
    out = parse_session_key("task:t-001:stage:research:sub:web-search")
    assert out == {"task_id": "t-001", "stage_id": "research", "sub_id": "web-search"}


def test_parse_unrelated_key_returns_empty():
    assert parse_session_key("random:key") == {}


# --------------------------------------------------------------------------- #
# Round-trip
# --------------------------------------------------------------------------- #


def test_round_trip_stage():
    key = build_stage_key("t-001", "research")
    parsed = parse_session_key(key)
    assert parsed == {"task_id": "t-001", "stage_id": "research"}


def test_round_trip_subtask():
    key = build_subtask_key("t-001", "research", "web-search")
    parsed = parse_session_key(key)
    assert parsed == {"task_id": "t-001", "stage_id": "research", "sub_id": "web-search"}
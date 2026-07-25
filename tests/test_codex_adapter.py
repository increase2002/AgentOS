"""Tests for CodexAdapter.

The subprocess is mocked via the `process_runner` config injection so we
do not need a real Codex CLI on PATH.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentos.drivers import CodexAdapter
from agentos.drivers.base import DriverError


def _runner_returning(stdout: str, stderr: str = "", rc: int = 0):
    """Build an async callable that returns (stdout, stderr, rc)."""

    async def _runner(argv: list[str], timeout_s: float):
        return stdout, stderr, rc

    return _runner


def test_codex_adapter_default_invocation() -> None:
    a = CodexAdapter("test", {"process_runner": _runner_returning("")})
    assert "codex" in a.cli_invocation
    assert "{prompt_file}" in a.cli_invocation


def test_codex_adapter_custom_invocation() -> None:
    a = CodexAdapter("test", {
        "cli_invocation": "codex chat --input {prompt_file}",
        "process_runner": _runner_returning(""),
    })
    assert a.cli_invocation == "codex chat --input {prompt_file}"


@pytest.mark.asyncio
async def test_codex_adapter_chat_writes_prompt_file() -> None:
    captured: dict[str, Any] = {}

    async def runner(argv: list[str], timeout_s: float):
        captured["argv"] = argv
        return ("", "", 0)

    a = CodexAdapter("test", {
        "cli_invocation": "codex --prompt-file {prompt_file} --json",
        "process_runner": runner,
    })
    await a.chat("hello world")
    argv = captured["argv"]
    assert "--prompt-file" in argv
    # The arg after --prompt-file is a temp .txt path
    idx = argv.index("--prompt-file")
    assert argv[idx + 1].endswith(".txt")


@pytest.mark.asyncio
async def test_codex_adapter_parses_jsonl_with_usage() -> None:
    stdout = (
        '{"text": "answer "}\n'
        '{"text": "is 42"}\n'
        '{"usage": {"prompt_tokens": 10, "completion_tokens": 5}}\n'
    )
    a = CodexAdapter("test", {"process_runner": _runner_returning(stdout)})
    result = await a.chat("what?")
    assert result.content == "answer is 42"
    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5}


@pytest.mark.asyncio
async def test_codex_adapter_handles_plain_text_output() -> None:
    a = CodexAdapter("test", {"process_runner": _runner_returning("plain text reply")})
    result = await a.chat("hi")
    assert result.content == "plain text reply"


@pytest.mark.asyncio
async def test_codex_adapter_exit_nonzero_raises() -> None:
    a = CodexAdapter("test", {
        "process_runner": _runner_returning("", "boom", 1),
    })
    with pytest.raises(DriverError, match="exit 1"):
        await a.chat("hi")


@pytest.mark.asyncio
async def test_codex_adapter_health_check_ok() -> None:
    a = CodexAdapter("test", {
        "process_runner": _runner_returning("Codex CLI v1.0"),
    })
    assert await a.health_check() is True


@pytest.mark.asyncio
async def test_codex_adapter_health_check_fail() -> None:
    a = CodexAdapter("test", {
        "process_runner": _runner_returning("", "not found", 127),
    })
    assert await a.health_check() is False


@pytest.mark.asyncio
async def test_codex_adapter_tool_subset_in_metadata() -> None:
    a = CodexAdapter("test", {"process_runner": _runner_returning("{}")})
    result = await a.chat("plan this", tool_subset=[])
    assert result.metadata["tool_subset"] == []


@pytest.mark.asyncio
async def test_codex_adapter_temp_file_cleaned_up() -> None:
    import os
    captured_paths: list[str] = []

    async def runner(argv: list[str], timeout_s: float):
        idx = argv.index("--prompt-file")
        captured_paths.append(argv[idx + 1])
        return ("", "", 0)

    a = CodexAdapter("test", {
        "cli_invocation": "codex --prompt-file {prompt_file}",
        "process_runner": runner,
    })
    await a.chat("temp")
    assert len(captured_paths) == 1
    # After chat() returns, the temp file should be gone
    assert not os.path.exists(captured_paths[0])
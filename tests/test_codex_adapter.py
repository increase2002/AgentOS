"""Tests for CodexAdapter.

The subprocess is mocked via the `process_runner` config injection so we
do not need a real Codex CLI on PATH. Default invocation is resolved at
__init__ time via ``_resolve_default_invocation`` (which targets
``node <codex.js> exec --json -`` on the current machine); tests that
need a stable template should override ``cli_invocation`` explicitly.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from agentos.drivers import CodexAdapter
from agentos.drivers.base import DriverError


def _runner_returning(stdout: str, stderr: str = "", rc: int = 0):
    """Build an async runner that returns (stdout, stderr, rc).

    Accepts ``stdin_bytes`` (and any other kwargs) so callers evolving
    the signature do not need to update mock fixtures.
    """

    async def _runner(argv: list[str], timeout_s: float, **_: Any):
        return stdout, stderr, rc

    return _runner


def test_codex_adapter_default_invocation_uses_node_and_exec() -> None:
    a = CodexAdapter("test", {"process_runner": _runner_returning("")})
    # Resolved default must (a) bypass the broken `codex.cmd` shim by
    # going through `node`, (b) target the non-interactive subcommand,
    # and (c) read the prompt from stdin via the `-` sentinel.
    assert "node" in a.cli_invocation.lower()
    assert "exec" in a.cli_invocation
    assert "--json" in a.cli_invocation
    assert a.cli_invocation.rstrip().endswith("-")


def test_codex_adapter_custom_invocation() -> None:
    a = CodexAdapter("test", {
        "cli_invocation": "codex chat --input {prompt_file}",
        "process_runner": _runner_returning(""),
    })
    assert a.cli_invocation == "codex chat --input {prompt_file}"


@pytest.mark.asyncio
async def test_codex_adapter_chat_writes_prompt_file() -> None:
    """Custom ``{prompt_file}`` invocations still get the file path."""
    captured: dict[str, Any] = {}

    async def runner(argv: list[str], timeout_s: float, **_: Any):
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
async def test_codex_adapter_chat_pipes_brief_to_stdin() -> None:
    """Default invocation must pipe the brief through stdin_bytes."""
    captured: dict[str, Any] = {}

    async def runner(argv: list[str], timeout_s: float, **kwargs: Any):
        captured["argv"] = argv
        captured["stdin_bytes"] = kwargs.get("stdin_bytes")
        return ("", "", 0)

    a = CodexAdapter("test", {"process_runner": runner})
    await a.chat("hello stdin")
    assert captured["stdin_bytes"] == b"hello stdin"


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
async def test_codex_adapter_parses_codex_exec_item_completed() -> None:
    """The canonical ``codex exec --json`` stream shape nests agent
    message text under ``item.text`` with ``item.type == agent_message``.
    Plain top-level ``text``/``content`` parsing must not catch it."""
    stdout = (
        '{"type":"thread.started","thread_id":"t1"}\n'
        '{"type":"turn.started"}\n'
        '{"type":"item.completed","item":{"id":"i0","type":"agent_message","text":"pong"}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":50,"output_tokens":3}}\n'
    )
    a = CodexAdapter("test", {"process_runner": _runner_returning(stdout)})
    result = await a.chat("ping")
    assert result.content == "pong"
    assert result.usage == {"input_tokens": 50, "output_tokens": 3}


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
        "process_runner": _runner_returning(""),
    })
    # rc=0 from the mock runner -> healthy regardless of stdout content.
    assert await a.health_check() is True


@pytest.mark.asyncio
async def test_codex_adapter_health_check_fail() -> None:
    a = CodexAdapter("test", {
        "process_runner": _runner_returning("", "", 1),
    })
    assert await a.health_check() is False


@pytest.mark.asyncio
async def test_codex_adapter_tool_subset_in_metadata() -> None:
    a = CodexAdapter("test", {"process_runner": _runner_returning("{}")})
    result = await a.chat("plan this", tool_subset=[])
    assert result.metadata["tool_subset"] == []


@pytest.mark.asyncio
async def test_codex_adapter_temp_file_cleaned_up() -> None:
    captured_paths: list[str] = []

    async def runner(argv: list[str], timeout_s: float, **_: Any):
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

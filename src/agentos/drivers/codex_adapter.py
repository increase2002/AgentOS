"""Codex CLI subprocess adapter.

Wraps the Codex CLI as a chat driver. The CLI is spawned per request
and its stdout is captured and parsed into a ChatResult.

NOTE: Codex CLI's exact flag set is NOT hardcoded because it varies by
version. Operator must provide a working `cli_invocation` template with
a `{prompt_file}` placeholder. The default assumes `codex` is on PATH
and supports `--prompt-file` + `--json` flags.

Brief is written to a temp file and passed via the placeholder; this
avoids argv-length limits and shell escaping issues with large briefs.

Streaming: stdout is parsed line-by-line. JSON events with `text` or
`content` fields are concatenated. A final event with `usage` (per
ADR config `usage_json_line=True`) is recorded in ChatResult.usage.

Refs: ADR-0001 (Integration Method), ADR-0007 (Driver Failure Policy).
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable

from agentos.drivers.base import BaseDriver, ChatResult, DriverError


# Type alias for the injectable subprocess runner. Default impl is below.
ProcessRunner = Callable[[list[str], float], Awaitable[tuple[str, str, int]]]


async def _default_process_runner(
    argv: list[str], timeout_s: float
) -> tuple[str, str, int]:
    """Default subprocess runner: asyncio.create_subprocess_exec with capture."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise DriverError(
            f"Codex CLI timed out after {timeout_s}s"
        ) from exc

    return (
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
        proc.returncode if proc.returncode is not None else -1,
    )


class CodexAdapter(BaseDriver):
    """Driver wrapping Codex CLI as a subprocess."""

    DEFAULT_INVOCATION = "codex --prompt-file {prompt_file} --json"

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name, config)
        self.cli_invocation: str = config.get(
            "cli_invocation", self.DEFAULT_INVOCATION
        )
        self.cli_timeout_s: float = float(config.get("cli_timeout_s", 120))
        self.usage_json_line: bool = bool(config.get("usage_json_line", True))
        self._runner: ProcessRunner = config.get(
            "process_runner", _default_process_runner
        )

    async def chat(
        self,
        brief: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
        session_key: str | None = None,
        tool_subset: list[str] | None = None,
    ) -> ChatResult:
        # Write brief to temp file. The placeholder {prompt_file} in the
        # invocation template is filled in below.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(brief)
            prompt_file = f.name

        try:
            invocation = self.cli_invocation.format(prompt_file=prompt_file)
            argv = shlex.split(invocation)
            stdout, stderr, returncode = await self._runner(
                argv, self.cli_timeout_s
            )

            if returncode != 0:
                raise DriverError(
                    f"CodexAdapter[{self.name}] CLI exit {returncode}: "
                    f"{stderr[:500]}"
                )

            content, usage = self._parse_output(stdout)
            return ChatResult(
                content=content or "(no content)",
                usage=usage,
                metadata={
                    "model": "codex-cli",
                    "finish_reason": "stop",
                    "session_key": session_key,
                    "tool_subset": tool_subset,
                    "cli_invocation": invocation,
                },
            )
        finally:
            try:
                os.unlink(prompt_file)
            except OSError:
                pass

    def _parse_output(self, stdout: str) -> tuple[str, dict[str, int] | None]:
        """Parse JSON-Lines / mixed output. Returns (content, usage)."""
        content_parts: list[str] = []
        usage: dict[str, int] | None = None
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # Non-JSON line: treat as plain text content
                content_parts.append(line)
                continue

            if not isinstance(event, dict):
                content_parts.append(str(event))
                continue

            # Extract text-like fields from JSON events
            text = event.get("text")
            if isinstance(text, str):
                content_parts.append(text)
            ev_content = event.get("content")
            if isinstance(ev_content, str):
                content_parts.append(ev_content)

            # Last usage wins (if config allows)
            if self.usage_json_line and isinstance(event.get("usage"), dict):
                u = event["usage"]
                usage = {
                    str(k): int(v)
                    for k, v in u.items()
                    if isinstance(v, (int, float))
                }

        return "".join(content_parts), usage

    async def health_check(self) -> bool:
        # Quick smoke: replace {prompt_file} with --help to avoid file creation.
        argv = shlex.split(
            self.cli_invocation.replace("{prompt_file}", "--help")
        )
        try:
            stdout, _, returncode = await self._runner(argv, 10.0)
        except Exception:
            return False
        return returncode == 0 or "codex" in stdout.lower()
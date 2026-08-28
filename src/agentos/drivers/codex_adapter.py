"""Codex CLI subprocess adapter.

Wraps the Codex CLI as a chat driver. The CLI is spawned per request
and its stdout is captured and parsed into a ChatResult.

NOTE: Codex CLI's exact flag set is NOT hardcoded because it varies by
version. Operator may supply a working ``cli_invocation`` template; the
default is resolved at ``__init__`` time and targets the non-interactive
``codex exec --json -`` subcommand (prompt via stdin).

Brief is always passed to the subprocess via stdin (avoids argv-length
limits and shell escaping issues with large briefs). Operators using a
custom ``cli_invocation`` template can still reference ``{prompt_file}``
to read the brief path instead.

Streaming: stdout is parsed line-by-line. JSON events with ``text`` or
``content`` fields are concatenated. A final event with ``usage`` (per
ADR config ``usage_json_line=True``) is recorded in ChatResult.usage.

Refs: ADR-0001 (Integration Method), ADR-0007 (Driver Failure Policy).
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable

from agentos.drivers.base import BaseDriver, ChatResult, DriverError
from agentos.telemetry.jsonl import install_telemetry


# Type alias for the injectable subprocess runner. Default impl is below.
# New ``stdin_bytes`` kwarg is optional; runners that don't need stdin can
# accept and ignore it. Tests injecting custom runners should add the
# kwarg too so call sites stay forward-compatible.
ProcessRunner = Callable[..., Awaitable[tuple[str, str, int]]]


def _locate_codex_js() -> str | None:
    """Find the absolute path to ``@openai/codex/bin/codex.js``.

    The npm-shipped ``codex.cmd`` shim wraps PowerShell and cannot be
    invoked from ``asyncio.create_subprocess_exec`` on Windows
    (``CreateProcess`` returns ``ERROR_FILE_NOT_FOUND`` because the
    ``.cmd`` contains ``#!/usr/bin/env pwsh`` rather than real
    cmd.exe content). We bypass the shim entirely and call the JS
    entry point via ``node``.

    Resolution order:
      1. Walk ``$PATH`` for an entry whose sibling ``node_modules``
         contains ``@openai/codex``.
      2. Probe the Windows npm-global prefix (``%APPDATA%\\npm`` and
         ``%LOCALAPPDATA%\\npm``) for the same layout.

    Returns:
        Absolute path to ``codex.js`` or ``None`` when not found.
    """
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        try:
            js = Path(d) / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
            if js.is_file():
                return str(js)
        except OSError:
            continue

    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(
            Path(appdata) / "npm" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        )
    localapp = os.environ.get("LOCALAPPDATA")
    if localapp:
        candidates.append(
            Path(localapp) / "npm" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        )
    for js in candidates:
        if js.is_file():
            return str(js)
    return None


async def _default_process_runner(
    argv: list[str],
    timeout_s: float,
    *,
    stdin_bytes: bytes | None = None,
) -> tuple[str, str, int]:
    """Default subprocess runner: asyncio.create_subprocess_exec with capture.

    When ``stdin_bytes`` is provided, the brief is piped to the child
    process via stdin (closed at EOF) so it can be consumed by CLI
    subcommands that read prompts from stdin (e.g.
    ``codex exec --json -``). Otherwise stdin is set to ``DEVNULL`` so
    the child never blocks on a tty.
    """
    stdin = (
        asyncio.subprocess.PIPE
        if stdin_bytes is not None
        else asyncio.subprocess.DEVNULL
    )
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=stdin,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(input=stdin_bytes),
            timeout=timeout_s,
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

    # NOTE: We intentionally do NOT default ``cli_invocation`` to a literal
    # ``codex --prompt-file ...`` string. Two problems with that template:
    #
    # 1. The npm-shipped ``codex.cmd`` shim contains PowerShell content
    #    and cannot be executed via ``asyncio.create_subprocess_exec`` on
    #    Windows (``CreateProcess`` returns ``ERROR_FILE_NOT_FOUND``).
    # 2. ``--prompt-file`` belongs to the interactive ``codex`` CLI; the
    #    non-interactive subcommand is ``codex exec`` which reads the
    #    prompt from a positional argument or stdin (``-`` sentinel).
    #
    # The default is resolved at ``__init__`` time via
    # ``_resolve_default_invocation`` which locates ``node`` +
    # ``@openai/codex/bin/codex.js`` and invokes the JS entry point with
    # ``codex exec --json -``. The brief is piped via stdin
    # (see ``_default_process_runner``). Operators with a custom
    # ``codex`` build can still pass ``cli_invocation`` explicitly.

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name, config)
        if "cli_invocation" in config:
            self.cli_invocation: str = config["cli_invocation"]
        else:
            self.cli_invocation = self._resolve_default_invocation()
        self.cli_timeout_s: float = float(config.get("cli_timeout_s", 120))
        self.usage_json_line: bool = bool(config.get("usage_json_line", True))
        self._runner: ProcessRunner = config.get(
            "process_runner", _default_process_runner
        )
        # ADR-0004: auto-wire telemetry hook so every chat() emits
        # DRIVER_CHAT_IN/OUT events to G:/AgentOS/telemetry/{date}.jsonl.
        install_telemetry(self)

    @staticmethod
    def _resolve_default_invocation() -> str:
        """Build a working ``node <codex.js> exec --json -`` template.

        The trailing ``-`` is the Codex CLI sentinel for "read prompt
        from stdin"; ``chat()`` writes the brief into stdin via the
        default process runner.

        Raises:
            DriverError: when ``node`` is not on ``PATH`` or the
                ``@openai/codex`` package cannot be located.
        """
        node = shutil.which("node")
        if not node:
            raise DriverError(
                "node not found on PATH; cannot run Codex CLI. "
                "Install Node.js 20+ then reinstall @openai/codex, or set "
                "CodexAdapter config['cli_invocation'] explicitly."
            )
        js_path = _locate_codex_js()
        if not js_path:
            raise DriverError(
                "Cannot locate @openai/codex install. "
                "Run `npm install -g @openai/codex` or set "
                "CodexAdapter config['cli_invocation'] to a working template."
            )
        # Double-quoted so shlex.split keeps each path as a single argv
        # element even when the path contains spaces.
        return f'"{node}" "{js_path}" exec --json -'

    async def chat(
        self,
        brief: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
        session_key: str | None = None,
        tool_subset: list[str] | None = None,
    ) -> ChatResult:
        # The brief is always piped to the child via stdin (UTF-8). This
        # works for the default ``codex exec --json -`` invocation and for
        # any custom invocation whose CLI reads its prompt from stdin.
        #
        # For backward compatibility we still write a temp file: an
        # operator-supplied ``cli_invocation`` may use the ``{prompt_file}``
        # placeholder to reference the brief path instead of stdin.
        stdin_bytes = brief.encode("utf-8")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(brief)
            prompt_file = f.name

        try:
            invocation = self.cli_invocation.format(prompt_file=prompt_file)
            argv = shlex.split(invocation)
            stdout, stderr, returncode = await self._runner(
                argv, self.cli_timeout_s, stdin_bytes=stdin_bytes,
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
        """Parse JSON-Lines / mixed output. Returns (content, usage).

        Supports three event shapes emitted by Codex CLI versions:

        1. ``{"text": "..."}`` / ``{"content": "..."}`` -- top-level
           text/content (older builds / generic LLM SDKs).
        2. ``{"type": "item.completed", "item": {"type": "agent_message",
           "text": "..."}}`` -- the canonical ``codex exec --json``
           streaming shape (one ``agent_message`` per turn's reply).
        3. ``{"usage": {...}}`` (alone or alongside ``turn.completed``) --
           final usage record; last one wins when ``usage_json_line`` is on.
        """
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

            # (2) codex exec --json: agent_message nested under item.
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str):
                    content_parts.append(text)

            # (1) legacy top-level text/content (older codex / custom CLIs).
            text = event.get("text")
            if isinstance(text, str):
                content_parts.append(text)
            ev_content = event.get("content")
            if isinstance(ev_content, str):
                content_parts.append(ev_content)

            # Usage: last event with a usage dict wins.
            if self.usage_json_line and isinstance(event.get("usage"), dict):
                u = event["usage"]
                usage = {
                    str(k): int(v)
                    for k, v in u.items()
                    if isinstance(v, (int, float))
                }

        return "".join(content_parts), usage

    async def health_check(self) -> bool:
        """Smoke check that ``node`` + the resolved Codex JS are launchable.

        We bypass the cli_invocation template (which may not have a
        ``{prompt_file}`` placeholder on the default path) and run a
        static ``node <codex.js> --version`` instead. The subprocess is
        not given stdin so it never blocks on a tty.
        """
        node = shutil.which("node")
        js_path = _locate_codex_js()
        if not node or not js_path:
            return False
        try:
            _, _, returncode = await self._runner(
                [node, js_path, "--version"], 10.0
            )
        except Exception:
            return False
        return returncode == 0
"""Tests for the agentos CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.cli import main as cli_main


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_send_text(tmp_path, capsys):
    bus = tmp_path / "bus.jsonl"
    rc = cli_main([
        "--bus", str(bus),
        "send", "--from", "codex", "--to", "openclaw", "--text", "hello world",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "sent" in out
    assert "codex -> openclaw" in out

    recs = _read_jsonl(bus)
    assert len(recs) == 1
    assert recs[0]["payload"]["text"] == "hello world"


def test_send_from_file(tmp_path):
    bus = tmp_path / "bus.jsonl"
    src = tmp_path / "brief.md"
    src.write_text("# plan content\n\nsteps", encoding="utf-8")

    rc = cli_main([
        "--bus", str(bus),
        "send", "--from", "openclaw", "--to", "codex", "--from-file", str(src),
    ])
    assert rc == 0

    recs = _read_jsonl(bus)
    assert recs[0]["payload"]["content"] == "# plan content\n\nsteps"
    assert recs[0]["payload"]["file"] == str(src)


def test_send_with_task_id(tmp_path):
    bus = tmp_path / "bus.jsonl"
    rc = cli_main([
        "--bus", str(bus),
        "send", "--from", "codex", "--to", "openclaw",
        "--text", "done",
        "--task", "t-001",
    ])
    assert rc == 0

    recs = _read_jsonl(bus)
    assert recs[0]["payload"]["task_id"] == "t-001"


def test_receive_empty(tmp_path, capsys):
    bus = tmp_path / "bus.jsonl"
    rc = cli_main(["--bus", str(bus), "receive", "--to", "codex"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no new messages" in out


def test_receive_filters_by_recipient(tmp_path, capsys):
    bus = tmp_path / "bus.jsonl"
    cli_main(["--bus", str(bus), "send",
              "--from", "a", "--to", "codex", "--text", "for codex"])
    cli_main(["--bus", str(bus), "send",
              "--from", "b", "--to", "openclaw", "--text", "for openclaw"])

    capsys.readouterr()  # clear send outputs
    rc = cli_main(["--bus", str(bus), "receive", "--to", "codex"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "for codex" in out
    assert "for openclaw" not in out


def test_inbox_summary(tmp_path, capsys):
    bus = tmp_path / "bus.jsonl"
    cli_main(["--bus", str(bus), "send", "--from", "a", "--to", "codex", "--text", "1"])
    cli_main(["--bus", str(bus), "send", "--from", "b", "--to", "codex", "--text", "2"])
    cli_main(["--bus", str(bus), "send", "--from", "c", "--to", "openclaw", "--text", "3"])

    rc = cli_main(["--bus", str(bus), "inbox"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "total: 3" in out
    assert "codex: 2" in out
    assert "openclaw: 1" in out


def test_search(tmp_path, capsys):
    bus = tmp_path / "bus.jsonl"
    cli_main(["--bus", str(bus), "send",
              "--from", "codex", "--to", "openclaw",
              "--text", "tool_subset enforcement"])
    cli_main(["--bus", str(bus), "send",
              "--from", "codex", "--to", "openclaw",
              "--text", "memory federation"])

    capsys.readouterr()
    rc = cli_main(["--bus", str(bus), "search", "tool_subset"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 match" in out


def test_show_by_task(tmp_path, capsys):
    bus = tmp_path / "bus.jsonl"
    cli_main(["--bus", str(bus), "send",
              "--from", "codex", "--to", "openclaw",
              "--text", "first task done", "--task", "t-001"])
    cli_main(["--bus", str(bus), "send",
              "--from", "openclaw", "--to", "codex",
              "--text", "ack t-001", "--task", "t-001"])
    cli_main(["--bus", str(bus), "send",
              "--from", "codex", "--to", "openclaw",
              "--text", "different task", "--task", "t-002"])

    capsys.readouterr()
    rc = cli_main(["--bus", str(bus), "show", "--task", "t-001"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "t-001" in out
    assert "first task done" in out or "2 message" in out
    assert "different task" not in out


def test_send_default_message_type(tmp_path):
    bus = tmp_path / "bus.jsonl"
    rc = cli_main(["--bus", str(bus), "send",
                   "--from", "codex", "--to", "openclaw",
                   "--text", "default handoff"])
    assert rc == 0
    recs = _read_jsonl(bus)
    assert recs[0]["type"] == "HANDOFF"
    assert recs[0]["priority"] == "NORMAL"


# ---------------------------------------------------------------------------
# Stdin support
# ---------------------------------------------------------------------------


def test_send_from_stdin(tmp_path, monkeypatch):
    """When no --text and no --from-file, CLI reads from stdin."""
    bus = tmp_path / "bus.jsonl"
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdin.read", lambda: "pasted from chatgpt\n")

    rc = cli_main([
        "--bus", str(bus),
        "send", "--from", "openclaw", "--to", "codex",
    ])
    assert rc == 0

    recs = _read_jsonl(bus)
    assert recs[0]["payload"]["text"] == "pasted from chatgpt\n"


def test_send_from_file_missing_gives_clear_error(tmp_path, capsys):
    """When --from-file points to a missing file, error message suggests workarounds."""
    bus = tmp_path / "bus.jsonl"
    missing = tmp_path / "does-not-exist.md"
    with pytest.raises(SystemExit) as exc_info:
        cli_main([
            "--bus", str(bus),
            "send", "--from", "openclaw", "--to", "codex",
            "--from-file", str(missing),
        ])
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "does not exist" in err
    assert "stdin" in err  # suggests stdin workaround


def test_send_no_body_gives_clear_error(tmp_path, capsys, monkeypatch):
    """When TTY (no body anywhere), error message explains all 3 options."""
    bus = tmp_path / "bus.jsonl"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    with pytest.raises(SystemExit) as exc_info:
        cli_main([
            "--bus", str(bus),
            "send", "--from", "openclaw", "--to", "codex",
        ])
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "--text" in err
    assert "--from-file" in err
    assert "stdin" in err
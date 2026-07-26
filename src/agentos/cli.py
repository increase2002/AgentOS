"""`agentos` CLI: send / receive / show / search / inbox.

Minimal interface for the JSONL message bus. Used to dogfood AgentOS
during development (老大 ferries messages between Codex and OpenClaw
via these commands instead of copy-pasting chat windows).

Entry point registered in pyproject.toml as `agentos`.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from agentos.bus.jsonl import DEFAULT_BUS_PATH, JSONLBus
from agentos.schemas.message import Message, MessageType, Priority


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentos",
        description="AgentOS bus CLI (JSONL append-only log).",
    )
    parser.add_argument(
        "--bus", type=Path, default=DEFAULT_BUS_PATH,
        help=f"Path to bus JSONL file (default: {DEFAULT_BUS_PATH})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # send
    p = sub.add_parser("send", help="Send a message")
    p.add_argument("--to", dest="to_agent", required=True)
    p.add_argument("--from", dest="from_agent", required=True)
    p.add_argument(
        "--type",
        choices=[t.value for t in MessageType],
        default=MessageType.HANDOFF.value,
    )
    p.add_argument(
        "--priority",
        choices=[p.value for p in Priority],
        default=Priority.NORMAL.value,
    )
    p.add_argument("--text", help="Message body (text)")
    p.add_argument(
        "--from-file",
        help="Path to a file whose contents become the payload (overrides --text)",
    )
    p.add_argument(
        "--task", help="Optional task_id to embed in payload (for `show --task` lookup)",
    )

    # receive
    p = sub.add_parser("receive", help="Show inbox messages")
    p.add_argument("--to", dest="to_agent", required=True)
    p.add_argument("--since", help="Only messages after this ID")
    p.add_argument("--limit", type=int, default=20, help="Max messages to show")

    # show
    p = sub.add_parser("show", help="Show all messages for a task")
    p.add_argument("--task", required=True)

    # search
    p = sub.add_parser("search", help="Full-text search")
    p.add_argument("query")

    # inbox
    p = sub.add_parser("inbox", help="Inbox summary (counts per recipient)")

    return parser


def cmd_send(args: argparse.Namespace) -> int:
    bus = JSONLBus(args.bus)
    payload: dict = {"task_id": args.task} if args.task else {}
    if args.from_file:
        content = Path(args.from_file).read_text(encoding="utf-8")
        payload["file"] = args.from_file
        payload["content"] = content
    else:
        payload["text"] = args.text or ""

    msg = Message(
        id=f"msg-{uuid.uuid4().hex[:12]}",
        from_agent=args.from_agent,
        to_agent=args.to_agent,
        type=MessageType(args.type),
        priority=Priority(args.priority),
        payload=payload,
    )
    bus.append(msg, artifact_ref=args.from_file)
    print(
        f"[bus] sent {msg.id}  "
        f"{msg.from_agent} -> {msg.to_agent}  "
        f"type={msg.type.value} priority={msg.priority.value}"
    )
    return 0


def _format_record(rec: dict) -> str:
    out = [
        f"=== {rec['id']} ({rec['created_at']}) ===",
        f"  from: {rec['from_agent']}  to: {rec['to_agent']}",
        f"  type: {rec['type']}  priority: {rec['priority']}",
    ]
    if rec.get("artifact_ref"):
        out.append(f"  artifact: {rec['artifact_ref']}")
    payload = rec.get("payload", {})
    if "text" in payload:
        out.append("")
        out.append(payload["text"])
    elif "content" in payload:
        out.append("")
        out.append(f"[from {payload.get('file', '?')}]")
        content = payload["content"]
        if len(content) > 800:
            content = content[:800] + "\n... [truncated]"
        out.append(content)
    return "\n".join(out)


def cmd_receive(args: argparse.Namespace) -> int:
    bus = JSONLBus(args.bus)
    msgs = bus.to_agent(args.to_agent, since_id=args.since)
    if not msgs:
        print(f"[inbox] {args.to_agent}: no new messages")
        return 0
    msgs = msgs[-args.limit:]
    for rec in msgs:
        print(_format_record(rec))
        print()
    print(f"[inbox] {len(msgs)} message(s)")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    bus = JSONLBus(args.bus)
    matching = [
        rec for rec in bus.iter_messages()
        if rec.get("payload", {}).get("task_id") == args.task
    ]
    if not matching:
        print(f"[show] task {args.task}: no messages")
        return 0
    print(f"=== Task {args.task} ({len(matching)} message(s)) ===")
    for rec in matching:
        print(
            f"  {rec['created_at']}  "
            f"{rec['from_agent']} -> {rec['to_agent']}  "
            f"[{rec['type']}]"
        )
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    bus = JSONLBus(args.bus)
    matches = bus.search(args.query)
    if not matches:
        print(f"[search] no matches for {args.query!r}")
        return 0
    print(f"[search] {len(matches)} match(es) for {args.query!r}:")
    for rec in matches:
        print(
            f"  {rec['id']}  {rec['from_agent']} -> {rec['to_agent']}  "
            f"[{rec['type']}]  {rec['created_at']}"
        )
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    bus = JSONLBus(args.bus)
    total = bus.count()
    summary = bus.summary()
    print(f"[bus] total: {total} message(s)")
    for agent, count in sorted(summary.items()):
        print(f"  to {agent}: {count}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "send": cmd_send,
        "receive": cmd_receive,
        "show": cmd_show,
        "search": cmd_search,
        "inbox": cmd_inbox,
    }
    return handlers[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
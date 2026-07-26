"""A2A Communication Bus.

The Bus is an append-only log of A2A messages (per schemas/message.py).
The minimal v0.1 implementation is a JSONL file: see :mod:`agentos.bus.jsonl`.
The Orchestrator Engine (ADR-0010) reads from this bus and dispatches to drivers.
"""

from agentos.bus.jsonl import DEFAULT_BUS_PATH, JSONLBus

__all__ = ["DEFAULT_BUS_PATH", "JSONLBus"]
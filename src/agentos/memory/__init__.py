"""Cross-agent Memory Service (per ADR-0005 + ADR-0011).

This package implements the Memory Federation layer:
- BaseMemoryDriver (abstract)
- EmptyMemoryDriver + per-vendor subclasses (Codex / Anthropic / Gemini)
- MemoryService (fan-out + normalize + cross-encoder rerank)
- CrossEncoderReranker (interface) + GPT4oMiniReranker + NullReranker

Refs: ADR-0005 (Memory Federation Plan B), ADR-0011 (Memory Backend Tiering).
"""

from agentos.memory.base import BaseMemoryDriver, MemoryHit, MemorySearchResult
from agentos.memory.empty_drivers import (
    AnthropicMemoryDriver,
    CodexMemoryDriver,
    EmptyMemoryDriver,
    GeminiMemoryDriver,
)
from agentos.memory.rerank import (
    CrossEncoderReranker,
    GPT4oMiniReranker,
    NullReranker,
)
from agentos.memory.service import MemoryService

__all__ = [
    "AnthropicMemoryDriver",
    "BaseMemoryDriver",
    "CodexMemoryDriver",
    "CrossEncoderReranker",
    "EmptyMemoryDriver",
    "GPT4oMiniReranker",
    "GeminiMemoryDriver",
    "MemoryHit",
    "MemorySearchResult",
    "MemoryService",
    "NullReranker",
]
"""A2A message schema - agent-to-agent communication on the Bus."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    """Core A2A message types."""

    TASK_REQUEST = "TASK_REQUEST"
    TASK_ACCEPT = "TASK_ACCEPT"
    TASK_PROGRESS = "TASK_PROGRESS"
    TASK_BLOCKED = "TASK_BLOCKED"
    KNOWLEDGE_SHARE = "KNOWLEDGE_SHARE"
    REVIEW_REQUEST = "REVIEW_REQUEST"
    DECISION = "DECISION"
    HANDOFF = "HANDOFF"


class Priority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class Message(BaseModel):
    """A2A message envelope on the Communication Bus."""

    id: str
    from_agent: str
    to_agent: str
    type: MessageType
    priority: Priority = Priority.NORMAL
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
"""Shared type aliases for agent state."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import Field

from app.strict_config import StrictConfigModel

AgentMode = Literal["chat", "investigation"]


class ChatMessage(TypedDict, total=False):
    role: Literal["system", "user", "assistant"]
    content: str
    tool_calls: list[dict[str, Any]]


class ChatMessageModel(StrictConfigModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)

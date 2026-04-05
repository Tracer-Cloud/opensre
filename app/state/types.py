"""Shared type aliases for agent state."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import Field

from app.strict_config import StrictConfigModel

EvidenceSource = Literal[
    "storage",
    "batch",
    "tracer_web",
    "cloudwatch",
    "aws_sdk",
    "knowledge",
    "grafana",
    "datadog",
    "honeycomb",
    "coralogix",
    "eks",
    "github",
    "sentry",
    "mongodb",
    "google_docs",
    "vercel",
    "opsgenie",
    "elasticsearch",
]

AgentMode = Literal["chat", "investigation"]


class ChatMessage(TypedDict, total=False):
    role: Literal["system", "user", "assistant"]
    content: str
    tool_calls: list[dict[str, Any]]


class ChatMessageModel(StrictConfigModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)

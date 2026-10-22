"""Provider-agnostic runtime message data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from core.llm.types import ToolCall

type MessageMetadata = dict[str, Any]
type ProviderMessage = dict[str, Any]
type RuntimeContent = str | list[dict[str, Any]] | None


@dataclass(frozen=True)
class UserRuntimeMessage:
    """User-visible runtime transcript entry."""

    content: RuntimeContent
    metadata: MessageMetadata = field(default_factory=dict)
    role: Literal["user"] = "user"


@dataclass(frozen=True)
class AssistantRuntimeMessage:
    """Assistant turn retained in runtime shape.

    ``provider_payload`` is optional provider continuity data. It is kept out of
    general app/session metadata and replayed only by provider adapters.
    ``provider_binding`` names the client that produced it (provider endpoint
    and model); a client with another binding rebuilds the message from
    ``content`` and ``tool_calls`` instead. ``None`` resends it to any client.
    """

    content: RuntimeContent
    tool_calls: tuple[ToolCall, ...] = ()
    provider_payload: ProviderMessage | None = None
    provider_binding: str | None = None
    metadata: MessageMetadata = field(default_factory=dict)
    role: Literal["assistant"] = "assistant"


@dataclass(frozen=True)
class ToolResultRuntimeMessage:
    """Tool-observation entry for one assistant tool-call batch."""

    tool_calls: tuple[ToolCall, ...]
    results: tuple[Any, ...]
    provider_payloads: tuple[ProviderMessage, ...] = ()
    metadata: MessageMetadata = field(default_factory=dict)
    role: Literal["tool_result"] = "tool_result"


@dataclass(frozen=True)
class AppRuntimeMessage:
    """App/session metadata that may optionally be made visible to the model."""

    app_type: str
    content: RuntimeContent
    include_in_context: bool = True
    details: Any = None
    metadata: MessageMetadata = field(default_factory=dict)
    role: Literal["app"] = "app"


type RuntimeMessage = (
    UserRuntimeMessage | AssistantRuntimeMessage | ToolResultRuntimeMessage | AppRuntimeMessage
)
type RuntimeMessageLike = RuntimeMessage | ProviderMessage

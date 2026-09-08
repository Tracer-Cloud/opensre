"""Shared LLM tool-calling DTOs."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, TypeAlias, runtime_checkable

from pydantic import BaseModel

ResolvedIntegrations: TypeAlias = dict[str, Any]  # noqa: UP040


@runtime_checkable
class SchemaDescribedTool(Protocol):
    """The three attributes a provider reads to build a tool-schema payload.

    Stated here rather than imported from the tool tier. Naming the tool union
    put ``core.llm`` on a path back into ``core.tool``, which closed a package
    cycle the moment ``core/tool/__init__`` grew imports — and it bought nothing:
    every provider client widened the parameter to ``list[Any]`` anyway. This is
    what ``_anthropic_tool_schema`` and ``build_converse_tool_specs`` actually
    read.
    """

    @property
    def name(self) -> str:
        """Identifier the provider and the model see."""

    @property
    def description(self) -> str:
        """One-line description sent to the model."""

    @property
    def public_input_schema(self) -> dict[str, Any]:
        """JSON Schema for the arguments, with internal parameters removed."""


class ModelType(StrEnum):
    """Configured model tiers for non-agent LLM clients."""

    REASONING = "reasoning"
    CLASSIFICATION = "classification"
    TOOLCALL = "toolcall"


@dataclass(frozen=True)
class LLMRoute:
    """The resolved provider/transport decision shared by every role this turn."""

    settings: Any
    provider: str  # runtime provider (after auth-method resolution)
    cli_provider_registration: Any | None
    use_litellm: bool


@dataclass(frozen=True)
class LLMResponse:
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: Prompt-cache counters from the provider usage payload, when reported.
    #: ``None`` means the provider sent no cache fields — distinct from 0.
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None


@dataclass
class ToolCall:
    """A single tool invocation requested by the LLM."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class AgentLLMResponse:
    """Response from the agent LLM, with optional tool calls."""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    # Raw provider message data for the next assistant turn.
    # Anthropic: list of content blocks (always populated).
    # OpenAI-compatible: dict with role/content/tool_calls, populated only when
    # provider-specific extras (e.g. Gemini's thought_signature) need to be
    # preserved; otherwise None and the assistant message is reconstructed via
    # build_assistant_message.
    raw_content: Any = None
    #: Prompt-cache counters from the provider usage payload, when reported.
    #: ``None`` means the provider sent no cache fields — distinct from 0.
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    #: Provider-reported usage for this call; ``None`` when the provider sent none.
    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@runtime_checkable
class AgentLLMClient(Protocol):
    """The tool-calling LLM contract the shared ``Agent`` loop depends on.

    Deliberately narrow: only the two methods ``Agent`` invokes on its ``llm``
    (``tool_schemas`` and ``invoke``). Provider message-shaping (
    ``build_assistant_message`` / ``build_tool_result_message``) rides a
    separate seam (``core.messages.convert_to_llm_messages``) whose signatures
    diverge across providers, so it is intentionally excluded here.

    Implementers: the SDK clients (Anthropic / OpenAI / Bedrock-Converse /
    CLI-backed) and the canned ``_StaticToolCallLLM`` used by the explicit
    ``!``/``/slash`` paths.
    """

    @property
    def model_id(self) -> str | None:
        """The provider model identifier, used for context-budget sizing (may be None)."""

    def tool_schemas(self, tools: Sequence[SchemaDescribedTool]) -> list[dict[str, Any]]:
        """Translate runtime tools into the provider's tool-schema payloads."""

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        """Run one provider request and return the assistant response."""


@runtime_checkable
class StreamingReasoningClient(Protocol):
    """The streaming LLM contract the conversational assistant answer depends on."""

    def invoke_stream(self, prompt_or_messages: Any) -> Iterator[str]:
        """Stream the assistant reply as text chunks."""


@runtime_checkable
class CliLLMClient(Protocol):
    """The prompt-in/response-out contract a CLI-backed subprocess client exposes."""

    def with_config(self, **kwargs: Any) -> CliLLMClient:
        """Return a client bound to the given per-call overrides."""

    def with_structured_output(self, model: type[BaseModel]) -> Any:
        """Return a client that parses replies into instances of ``model``."""

    def invoke(self, prompt_or_messages: Any) -> LLMResponse:
        """Run one subprocess CLI invocation and return its response."""

    def invoke_stream(self, prompt_or_messages: Any) -> Iterator[str]:
        """Stream the assistant reply as text chunks."""


__all__ = [
    "AgentLLMClient",
    "AgentLLMResponse",
    "CliLLMClient",
    "LLMResponse",
    "LLMRoute",
    "ModelType",
    "ResolvedIntegrations",
    "StreamingReasoningClient",
    "ToolCall",
]

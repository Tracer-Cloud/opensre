"""What the ReAct loop needs from whoever runs it.

The loop calls back out for a handful of things — emitting events, narrowing the
tool list, queued follow-ups, and the optional provider hooks. ``LoopHost``
is that set of callbacks as a ``Protocol``: ``run_react_loop`` depends only on it
(plus an ``AgentRunInput``), so it never has to know about ``Agent`` — any object
with these methods can drive the loop. ``Agent`` is the usual one.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from core.events import RuntimeEvent
from core.llm.types import ToolCall
from core.messages import ProviderMessage, RuntimeMessage
from core.provider import ProviderRequest
from core.tool.contracts import RuntimeTool
from core.tool.execution import ToolExecutionHooks, ToolExecutionResult


class LoopHost[RuntimeToolT: RuntimeTool](Protocol):
    """The narrow set of hooks ``run_react_loop`` calls back into.

    ``core.agent.Agent`` implements this via ``EventEmitterMixin``,
    ``ToolFilterMixin``, ``SteeringMixin`` (``core.agent.mixins``), plus thin
    ``ProviderHookDelegate`` forwarders (``_transform_messages`` /
    ``_convert_to_llm`` / ``_before_request`` / ``_after_response``). The
    provider-hook delegate's concrete type is deliberately *not* part of this
    contract — only the method calls are — so a host can wire the four seams
    however it likes.
    """

    _tool_hooks: ToolExecutionHooks

    def _filter_tools(self, tools: list[RuntimeToolT]) -> list[RuntimeToolT]:
        """Narrow the tool list the loop may call this iteration."""

    def _emit_runtime(self, event: RuntimeEvent) -> None:
        """Publish one runtime event to whoever is watching the turn."""

    def _drain_steering_messages(self, messages: list[RuntimeMessage]) -> None:
        """Append any queued steering messages, in place."""

    def _pop_follow_up_message(self) -> str | None:
        """The next queued follow-up prompt, or None when there is none."""

    def _should_accept_conclusion(
        self,
        *,
        evidence_count: int,
        iteration: int,
        final_text: str = "",
        tool_results: Sequence[tuple[ToolCall, ToolExecutionResult]] = (),
    ) -> tuple[bool, str | None]:
        """Whether a no-tool reply may end the turn.

        Return ``(True, None)`` to accept, or ``(False, nudge)`` to inject a
        user message and continue. Hosts without a reviewed goal always accept.
        """

    def _transform_messages(self, messages: list[RuntimeMessage]) -> list[RuntimeMessage]:
        """Adjust runtime messages before they are converted for the provider."""

    def _convert_to_llm(self, llm: Any, messages: list[RuntimeMessage]) -> list[ProviderMessage]:
        """Render runtime messages in the provider's wire shape."""

    def _before_request(self, request: ProviderRequest) -> ProviderRequest:
        """Last chance to alter the request before it is sent."""

    def _after_response(self, request: ProviderRequest, response: Any) -> Any:
        """Inspect or replace the provider response before the loop reads it."""


__all__ = ["LoopHost"]

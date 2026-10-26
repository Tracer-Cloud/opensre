"""Optional hooks for building a :class:`HeadlessAgent`.

``None`` on a field means the host's usual defaults. For
``apply_capability_policy``, ``None`` means do not mutate the session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from core.agent_harness.ports import (
    ErrorReporter,
    PromptContextProvider,
    SessionState,
    SubprocessPresenterFactory,
    ToolEventObserver,
    ToolProvider,
)


class BuildTools(Protocol):
    """``(session, console, logger, observer) -> ToolProvider``.

    Positional-only: every caller passes these in order, so an implementation
    is free to name an argument it ignores ``_logger`` without failing the
    protocol on a name it never reads.
    """

    def __call__(
        self,
        session: SessionState,
        console: Any,
        logger: logging.Logger,
        observer: ToolEventObserver | None,
        /,
    ) -> ToolProvider:
        """Return the tools for this session."""


class BuildPrompts(Protocol):
    """``(session) -> PromptContextProvider``."""

    def __call__(self, session: SessionState, /) -> PromptContextProvider:
        """Return the prompt context for this session."""


class DescribeTool(Protocol):
    """``(tool_name) -> (display name, description)``, empty when unknown.

    The host renders live tool status but must not read the tool registry
    itself; a composer that may see both hands this in.
    """

    def __call__(self, tool_name: str, /) -> tuple[str, ...]:
        """Return the tool's own wording for status copy."""


class ApplyCapabilityPolicy(Protocol):
    """``(session) -> None``. ``None`` on the config field means do not call."""

    def __call__(self, session: SessionState, /) -> None:
        """Mutate ``session`` capabilities, or do nothing."""


@dataclass(frozen=True)
class AgentBuildConfig:
    """How a host wants its headless agent built.

    Omit a field to keep that host's usual default. ``apply_capability_policy``
    left unset means do not mutate the session.
    """

    build_tools: BuildTools | None = None
    build_prompts: BuildPrompts | None = None
    error_reporter: ErrorReporter | None = None
    apply_capability_policy: ApplyCapabilityPolicy | None = None
    #: Live tool-status wording and subprocess rendering. Both reach the tool
    #: tier, which the host sits below, so a composer supplies them.
    describe_tool: DescribeTool | None = None
    subprocess_presenter_factory: SubprocessPresenterFactory | None = None


__all__ = [
    "AgentBuildConfig",
    "ApplyCapabilityPolicy",
    "BuildPrompts",
    "BuildTools",
    "DescribeTool",
]

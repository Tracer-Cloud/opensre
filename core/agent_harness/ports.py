"""Ports (structural Protocols) the agentic turn engine talks to.

These are the seams that keep ``agent/`` decoupled from any concrete surface.
The interactive shell implements them as adapters over its ``Session``,
Rich console, tool registry, and grounding caches; the headless adapters in
:mod:`core.agent_harness.turns.headless_agent` implement minimal in-memory versions for API / test runs.

Nothing here imports ``interactive_shell``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from config.llm_reasoning_effort import ReasoningEffortChoice
from core.agent_harness.turns.gather_observation import GatheredEvidence
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from core.llm.types import AgentLLMClient
from core.tool.execution import ToolExecutionHooks

# A tool-loop event callback: ``(kind, data)`` where kind is e.g. "tool_start".
ToolEventObserver = Callable[[str, dict[str, Any]], None]

# Confirmation prompt: given a summary, return the user's response string.
ConfirmFn = Callable[[str], str]

# Builds the LLM client the action runner drives; hosts and tests inject one
# to replace the configured provider. The loop calls ``invoke`` and
# ``tool_schemas`` and nothing else, which is what ``AgentLLMClient`` states.
LlmFactory = Callable[[], AgentLLMClient]


@runtime_checkable
class OutputSink(Protocol):
    """Where the engine renders user-facing output."""

    def print(self, message: str = "") -> None:
        """Print one line of (markup-bearing) text."""

    def render_response_header(self, label: str) -> None:
        """Render the assistant response header (e.g. a labelled rule)."""

    def render_error(self, message: str) -> None:
        """Render an error/notice line."""

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
        defer_want_me_to_closer: bool = False,
    ) -> str:
        """Stream ``chunks`` to the surface under ``label`` and return the text.

        When ``defer_want_me_to_closer`` is true, surfaces may hold the Want-me-to
        closer until ``finish_streamed_response`` (optional sink method; gather
        normalize path).
        """


@runtime_checkable
class SessionState(Protocol):
    """Mutable per-session state the engine reads and writes.

    ``Session`` satisfies this structurally. The fields mirror what the
    action driver, the three-path engine, and the gather loop touch.
    """

    # --- turn-context snapshot fields ---
    cli_agent_messages: list[tuple[str, str]]
    configured_integrations_known: bool

    # Read-only here; ``Session`` stores a tuple. A property matches
    # covariantly, so any concrete ``Sequence[str]`` implementation satisfies it.
    @property
    def configured_integrations(self) -> Sequence[str]:
        raise NotImplementedError

    reasoning_effort: ReasoningEffortChoice | None

    # --- turn execution state ---
    history: list[dict[str, Any]]
    last_command_observation: str | None
    session_id: str

    # --- gather caches ---
    resolved_integrations_cache: dict[str, Any] | None
    vcs_repo_scopes: dict[str, tuple[str, ...]]
    active_vcs_repositories: dict[str, str]
    known_vcs_repo_scopes: dict[str, dict[str, tuple[str, ...]]]

    def record(self, kind: str, text: str, *, ok: bool = True) -> None:
        """Append a record of an executed action/turn to the session log."""


@runtime_checkable
class SessionBindable(Protocol):
    """Port that can retarget a session when the agent is reused across turns.

    Pooled / multi-turn hosts call :meth:`bind_session` when
    ``SessionManager.resolve`` returns a fresh session object for the same id.
    Ports that ignore session identity need not implement this; ``HeadlessAgent``
    only invokes it when the port structurally matches.
    """

    def bind_session(self, session: SessionState) -> None:
        """Point this port at ``session`` (same logical session, new object)."""


@runtime_checkable
class CancelCapableConsole(Protocol):
    """Console that exposes a cancellation flag and basic print capability."""

    @property
    def cancel_requested(self) -> bool:
        """True if the user requested to cancel the current operation."""

    def print(self, *args: Any, **kwargs: Any) -> None:
        """Print output to the console."""


@runtime_checkable
class ConsoleBindable(Protocol):
    """Tool port that can retarget the turn console (cancel / TTY observers).

    Gateway binds a per-turn ``CancelConsole`` so ``cancel_requested`` tracks
    the shared ``sink.turn_cancel`` Event for that message.
    """

    def bind_console(self, console: CancelCapableConsole) -> None:
        """Point tool UI / cancel probes at ``console`` for this turn."""


@runtime_checkable
class OutputBindable(Protocol):
    """Port that holds an :class:`OutputSink` and can retarget it across turns.

    ``HeadlessAgent.bind_turn(output=…)`` updates the agent's sink and must
    retarget every port that cached the previous sink (e.g. reasoning error
    rendering). Gateway usually keeps a stable ``BindableOutput`` and rebinds
    the outer transport output via ``BindableOutput.bind`` — that path does not
    need ``bind_turn(output=)``. Hosts that swap the ``OutputSink`` object
    itself must pass ``output=`` so :class:`OutputBindable` ports follow.
    """

    def bind_output(self, output: OutputSink) -> None:
        """Point this port at ``output`` for the current turn."""


@runtime_checkable
class ToolProvider(Protocol):
    """Supplies the action-agent tools and the per-turn tool-event observer."""

    def action_tools(
        self,
        *,
        confirm_fn: ConfirmFn | None,
        is_tty: bool | None,
        resolved_integrations: dict[str, Any] | None = None,
        turn_user_message: str = "",
    ) -> list[Any]:
        """Return the agent tools available for this turn.

        When ``resolved_integrations`` is supplied it is the turn's single
        resolved-integration view (from ``TurnSnapshot``); the provider builds
        tools from it instead of resolving again, so tools and the prompt agree.

        ``turn_user_message`` is what the user sent this turn; tools that must
        not repeat work the message already settled (an answered menu) read it
        from the scope.
        """

    def tool_resources(self) -> dict[str, Any]:
        """Return non-serializable resources for tools that opt into runtime context."""

    def observer(self, *, message: str) -> ToolEventObserver:
        """Return a tool-event observer for this turn (e.g. terminal renderer)."""


@runtime_checkable
class ErrorReporter(Protocol):
    """Reports caught exceptions (telemetry / logging)."""

    def report(self, exc: BaseException, *, context: str, expected: bool = False) -> None:
        raise NotImplementedError


@runtime_checkable
class PromptContextProvider(Protocol):
    """Supplies grounding text for the conversational assistant prompt.

    The grounding corpora (CLI reference, repo map, docs, environment) are
    surface/repo content; the shell adapter wires its grounding caches, the
    headless adapter returns empty strings.
    """

    def surface(self) -> str:
        """Which surface this turn runs on; defaults to the interactive shell."""
        return "interactive_shell"

    def cli_reference(self) -> str:
        raise NotImplementedError

    def agents_md(self) -> str:
        raise NotImplementedError

    def docs(self, query: str) -> str:
        raise NotImplementedError

    def runtime_facts(self) -> Mapping[str, Any]:
        """Runtime facts for this turn: session metadata plus fresh live values."""
        raise NotImplementedError

    def environment_block(self, runtime: Mapping[str, Any] | None = None) -> str:
        """Static environment block; ``runtime`` reuses the turn's capture."""
        raise NotImplementedError

    def long_term_memory(self) -> str:
        raise NotImplementedError

    def setup_state(self) -> str:
        """The operator's connected integrations and schedules, as a fact block."""

    def log_diagnostics(self, reason: str) -> None:
        raise NotImplementedError


class ExecuteActions(Protocol):
    """Bound action tool-calling driver handed to ``run_turn``."""

    def __call__(
        self,
        text: str,
        *,
        confirm_fn: ConfirmFn | None = None,
        is_tty: bool | None = None,
        turn_plan: Any = None,
    ) -> ToolCallingTurnResult:
        """Run the action tool-calling turn for ``text``."""


@runtime_checkable
class TurnAccounting(Protocol):
    """Records analytics/telemetry for a turn and finalizes the result."""

    def record_action_result(self, action_result: ToolCallingTurnResult) -> None:
        raise NotImplementedError

    def finalize(self, result: TurnResult) -> TurnResult:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class TurnBinding:
    """Everything a host binds on an agent for one turn, stated whole.

    A binding replaces the previous turn's values rather than layering on
    them, so a host cannot inherit another conversation's hooks or callback
    by omission. ``session`` and ``output`` are identity ports: ``None`` keeps
    the agent's current one. Every other field is the turn's value — ``None``
    means "none this turn" (no approval hooks, no confirmation callback, tty
    unknown), not "leave alone".
    """

    session: SessionState | None = None
    output: OutputSink | None = None
    accounting: TurnAccounting | None = None
    tool_hooks: ToolExecutionHooks | None = None
    console: CancelCapableConsole | None = None
    confirm_fn: ConfirmFn | None = None
    is_tty: bool | None = None


__all__ = [
    "CancelCapableConsole",
    "ConfirmFn",
    "ConsoleBindable",
    "ErrorReporter",
    "ExecuteActions",
    "GatheredEvidence",
    "LlmFactory",
    "LlmProviderPortsFactory",
    "OutputBindable",
    "OutputSink",
    "PromptContextProvider",
    "SessionBindable",
    "SessionState",
    "SlashPortsFactory",
    "SubprocessPresenterFactory",
    "TaskCancelPortsFactory",
    "ToolEventObserver",
    "ToolProvider",
    "TurnAccounting",
    "TurnBinding",
]


# Builds the presenter that streams a subprocess tool's output. The concrete
# presenter lives in ``tools`` (process helpers), so this seam is how a host
# hands one to the agent without ``core`` importing ``tools``.
SubprocessPresenterFactory = Callable[
    [Any, Any, "ConfirmFn | None", bool | None, bool],
    Any,
]


# Host capabilities an action tool calls back into: named commands, LLM-provider
# switching, and task cancellation. Their contracts live in
# ``tools`` beside the tools that call them (see
# ``tools.interactive_shell.shared.host_contracts.ExecutionGate``), and naming those
# Protocols here would mean ``core`` importing ``tools``.
#
# The return is ``object``, not ``Any``: ``core`` calls the factory and hands the
# result to ``ActionToolScope`` without reading a single attribute, and
# ``object`` is the type that says so. ``Any`` would silence a typo here as
# readily as it silences the import ``core`` is avoiding.
LlmProviderPortsFactory = Callable[[], object]
TaskCancelPortsFactory = Callable[[], object]
SlashPortsFactory = Callable[[], object]

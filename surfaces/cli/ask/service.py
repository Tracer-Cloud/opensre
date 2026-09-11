"""Run one configured agent turn and normalize its CLI outcome."""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from io import StringIO
from typing import Any

from rich.console import Console

from core.agent_harness import (
    AgentSession,
    SessionConfig,
    SessionCore,
    SessionManager,
    TurnResult,
)
from core.agent_harness.ports import ToolEventObserver
from core.agent_harness.spi.cancel import ensure_turn_cancel
from core.tool import ToolExecutionHooks
from infrastructure.errors import OpenSREError
from surfaces.cli.ask.approval import ApprovalTracker, build_approval_hooks
from surfaces.cli.ask.signals import AskSignal, ask_signal_scope

#: Capabilities the one-shot ``ask`` agent must not reach — it answers or runs a
#: bounded action, not slash commands or task cancellation.
_ASK_DISABLED_CAPABILITIES = ("llm_provider", "slash_commands", "task_cancel")


class AskStatus(StrEnum):
    SUCCESS = "success"
    APPROVAL_DENIED = "approval_denied"
    ERROR = "error"
    CANCELLED = "cancelled"


class AskExitCode(IntEnum):
    SUCCESS = 0
    ERROR = 1
    APPROVAL_DENIED = 3
    SIGINT = 130
    SIGTERM = 143


@dataclass(frozen=True, slots=True)
class AskError:
    """Stable structured error returned by ``opensre --json ask``."""

    message: str
    suggestion: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"message": self.message, "suggestion": self.suggestion}


@dataclass(frozen=True, slots=True)
class AskOutcome:
    """Normalized process outcome for one ask invocation."""

    status: AskStatus
    response: str
    denied_tools: tuple[str, ...] = ()
    error: AskError | None = None
    exit_code: AskExitCode = AskExitCode.SUCCESS

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "response": self.response,
            "denied_tools": list(self.denied_tools),
            "error": self.error.as_dict() if self.error is not None else None,
        }


class _CancellableConsole:
    def __init__(self, cancel_event: threading.Event) -> None:
        self._cancel_event = cancel_event
        self._console = Console(force_terminal=False, file=StringIO())

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._console, name)


class _AskOutputSink:
    """Discard intermediate rendering while preserving streamed answer text."""

    def print(self, message: str = "") -> None:
        _ = message

    def render_response_header(self, label: str) -> None:
        _ = label

    def render_error(self, message: str) -> None:
        _ = message

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
        defer_want_me_to_closer: bool = False,
    ) -> str:
        _ = (label, suppress_if_starts_with, defer_want_me_to_closer)
        return "".join(str(chunk) for chunk in chunks)

    def finish_streamed_response(self, answer: str) -> None:
        _ = answer


def _restrict_ask_capabilities(session: SessionCore) -> None:
    """Zero the capabilities the one-shot ask agent must not use."""
    for capability in _ASK_DISABLED_CAPABILITIES:
        session.available_capabilities[capability] = ()


def _run_agent_turn(
    prompt: str,
    hooks: ToolExecutionHooks,
    *,
    tool_event_observer: ToolEventObserver | None = None,
) -> TurnResult:
    manager = SessionManager()
    output = _AskOutputSink()
    cancel_event = ensure_turn_cancel(output)
    console = _CancellableConsole(cancel_event)
    session: SessionCore | None = None
    try:
        with ask_signal_scope(cancel_event):
            agent_session = AgentSession.start(
                SessionConfig(
                    load_env=True,
                    hydrate_integrations=True,
                    warm_integrations=True,
                    persistent_tasks=False,
                    open_store=False,
                    session_manager=manager,
                ),
                output=output,
                prepare_session=_restrict_ask_capabilities,
                console=console,
                is_tty=False,
                tool_hooks=hooks,
                tool_event_observer=tool_event_observer,
            )
            session = agent_session.bound_session
            # chat_until_goal, not chat: the agent can attach a session goal,
            # which must run to completion rather than stop after one turn.
            return agent_session.chat_until_goal(prompt).last_result
    finally:
        if session is not None:
            manager.close(session, extract_memory=False)


def _successful_turn(result: TurnResult) -> bool:
    action = result.action_result
    action_ok = (
        action.handled
        and not action.has_unhandled_clause
        and not action.hit_iteration_cap
        and action.accounting_status == "completed"
    )
    return bool(
        (result.answered or action_ok)
        and not action.hit_iteration_cap
        and not result.cancelled
        and result.primary_response_text
    )


def _approval_denied_message(denied_tools: tuple[str, ...]) -> str:
    """Name the denied tools and the exact flags that authorize them.

    A one-shot ``ask`` has no prompt to approve at, so a bare "denied" is a
    dead end; point the user straight at the flags that unblock the run.
    """
    tools = ", ".join(denied_tools)
    allow = " ".join(f"--allowed-tool {tool}" for tool in denied_tools)
    return (
        f"Approval denied for: {tools}\n"
        f"Re-run with {allow} to authorize these, "
        "or --dangerously-bypass-approvals to allow all."
    )


def _approval_denied_outcome(
    tracker: ApprovalTracker,
    *,
    response: str = "",
) -> AskOutcome | None:
    denied_tools = tracker.denied_tools
    if not denied_tools:
        return None
    return AskOutcome(
        status=AskStatus.APPROVAL_DENIED,
        response=response or _approval_denied_message(denied_tools),
        denied_tools=denied_tools,
        exit_code=AskExitCode.APPROVAL_DENIED,
    )


def cancelled_outcome(signum: int) -> AskOutcome:
    """Return the stable cancelled result for an ask process signal."""
    code = AskExitCode.SIGINT if signum == signal.SIGINT else AskExitCode.SIGTERM
    return AskOutcome(
        status=AskStatus.CANCELLED,
        response="Agent execution cancelled.",
        exit_code=code,
    )


def run_ask(
    prompt: str,
    *,
    allowed_tools: tuple[str, ...],
    bypass_approvals: bool,
    tool_event_observer: ToolEventObserver | None = None,
) -> AskOutcome:
    """Execute one ask turn with invocation-scoped approval authority."""
    tracker = ApprovalTracker()
    hooks = build_approval_hooks(
        allowed_tools=allowed_tools,
        bypass_approvals=bypass_approvals,
        tracker=tracker,
    )
    try:
        result = _run_agent_turn(prompt, hooks, tool_event_observer=tool_event_observer)
    except AskSignal as exc:
        return cancelled_outcome(exc.signum)
    except OpenSREError as exc:
        denied = _approval_denied_outcome(tracker)
        if denied is not None:
            return denied
        return AskOutcome(
            status=AskStatus.ERROR,
            response="",
            error=AskError(message=exc.message, suggestion=exc.suggestion),
            exit_code=AskExitCode.ERROR,
        )
    except Exception as exc:
        denied = _approval_denied_outcome(tracker)
        if denied is not None:
            return denied
        from surfaces.cli.error_mapping import reraise_cli_runtime_error

        try:
            reraise_cli_runtime_error(exc)
        except OpenSREError as mapped:
            return AskOutcome(
                status=AskStatus.ERROR,
                response="",
                error=AskError(
                    message=mapped.message,
                    suggestion=mapped.suggestion,
                ),
                exit_code=AskExitCode.ERROR,
            )
        except Exception as unmapped:
            exc = unmapped
        from surfaces.cli.telemetry import report_exception

        report_exception(exc, context="surfaces.cli.ask")
        return AskOutcome(
            status=AskStatus.ERROR,
            response="",
            error=AskError(message=str(exc) or type(exc).__name__),
            exit_code=AskExitCode.ERROR,
        )

    denied = _approval_denied_outcome(
        tracker,
        response=result.primary_response_text,
    )
    if denied is not None:
        return denied
    if result.cancelled:
        return AskOutcome(
            status=AskStatus.CANCELLED,
            response=result.primary_response_text or "Agent execution cancelled.",
            exit_code=AskExitCode.SIGINT,
        )
    if not _successful_turn(result):
        response = result.primary_response_text
        return AskOutcome(
            status=AskStatus.ERROR,
            response=response,
            error=AskError(message=response or "The agent did not complete the request."),
            exit_code=AskExitCode.ERROR,
        )
    return AskOutcome(
        status=AskStatus.SUCCESS,
        response=result.primary_response_text,
    )


__all__ = [
    "AskError",
    "AskExitCode",
    "AskOutcome",
    "AskSignal",
    "AskStatus",
    "ask_signal_scope",
    "cancelled_outcome",
    "run_ask",
]

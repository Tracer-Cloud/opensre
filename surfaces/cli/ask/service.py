"""Run one configured agent turn and normalize its CLI outcome."""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from functools import partial
from io import StringIO
from typing import Any
from uuid import uuid4

from rich.console import Console

from core.agent_harness import (
    AgentSession,
    PromptSurface,
    SessionConfig,
    SessionCore,
    SessionManager,
    TurnResult,
)
from core.agent_harness.ports import ToolEventObserver
from core.agent_harness.spi.cancel import ensure_turn_cancel
from core.agent_harness.spi.handoff import (
    apply_pending_user_choice_state,
    pending_user_choice_state_snapshot,
)
from core.agent_harness.spi.session_state import PendingUserChoice
from core.tool import SideEffectLevel, ToolExecutionHooks
from infrastructure.errors import OpenSREError
from surfaces.cli.ask.approval import ApprovalTracker, build_approval_hooks
from surfaces.cli.ask.file_input import AskFileInput, render_prompt_with_context
from surfaces.cli.ask.session import (
    ask_session_lock as _ask_session_lock,
)
from surfaces.cli.ask.session import (
    resolve_resume_session_id as _resolve_resume_session_id,
)
from surfaces.cli.ask.session import (
    resume_prompt as _resume_prompt,
)
from surfaces.cli.ask.signals import AskSignal, ask_signal_scope

#: Capabilities the one-shot ``ask`` agent must not reach — it answers or runs a
#: bounded action, not slash commands or task cancellation.
_ASK_DISABLED_CAPABILITIES = ("llm_provider", "slash_commands", "task_cancel")


class AskStatus(StrEnum):
    SUCCESS = "success"
    NEEDS_INPUT = "needs_input"
    APPROVAL_DENIED = "approval_denied"
    ERROR = "error"
    CANCELLED = "cancelled"


class AskExitCode(IntEnum):
    SUCCESS = 0
    ERROR = 1
    APPROVAL_DENIED = 3
    NEEDS_INPUT = 4
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
class AskQuestion:
    """One structured question returned by a headless ask invocation."""

    label: str
    title: str
    options: tuple[str, ...]
    multi_select: bool = False
    allow_custom: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "title": self.title,
            "options": list(self.options),
            "multi_select": self.multi_select,
            "allow_custom": self.allow_custom,
        }


@dataclass(frozen=True, slots=True)
class AskOutcome:
    """Normalized process outcome for one ask invocation."""

    status: AskStatus
    response: str
    denied_tools: tuple[str, ...] = ()
    session_id: str | None = None
    questions: tuple[AskQuestion, ...] = ()
    error: AskError | None = None
    exit_code: AskExitCode = AskExitCode.SUCCESS

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "response": self.response,
            "denied_tools": list(self.denied_tools),
            "session_id": self.session_id,
            "questions": [question.as_dict() for question in self.questions],
            "error": self.error.as_dict() if self.error is not None else None,
        }


@dataclass(slots=True)
class _AskRunState:
    """Session details captured before the manager releases its handle."""

    session_id: str | None = None
    pending_choice: PendingUserChoice | None = None
    pending_choice_is_new: bool = False


@dataclass(slots=True)
class _ResumedMutationTracker:
    """Remember whether a resumed turn reached potentially mutating work."""

    potential_mutation_started: bool = False


def _track_resumed_mutations(
    hooks: ToolExecutionHooks,
    tracker: _ResumedMutationTracker,
) -> ToolExecutionHooks:
    """Mark a resumed choice irreversible only before potentially mutating work."""
    before_tool_call = hooks.before_tool_call

    def before(request: Any) -> Any:
        decision = before_tool_call(request) if before_tool_call is not None else None
        if (decision is None or not decision.blocked) and request.tool.side_effect_level not in {
            SideEffectLevel.NONE,
            SideEffectLevel.READ_ONLY,
        }:
            # ``execute_tool_calls`` invokes this hook only after batch/schema
            # validation, immediately before it can dispatch the runtime tool.
            tracker.potential_mutation_started = True
        return decision

    return ToolExecutionHooks(
        before_tool_call=before,
        after_tool_call=hooks.after_tool_call,
        on_tool_update=hooks.on_tool_update,
        before_tool_batch=hooks.before_tool_batch,
    )


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
    """Discard intermediate rendering while retaining the terminal-visible answer."""

    def __init__(self) -> None:
        self._rendered_event = ""
        self._completed_turn = False

    def print(self, message: str = "") -> None:
        _ = message

    def render_response_header(self, label: str) -> None:
        _ = label

    def render_error(self, message: str) -> None:
        if message.strip():
            self._rendered_event = message

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
        defer_want_me_to_closer: bool = False,
    ) -> str:
        _ = (label, suppress_if_starts_with, defer_want_me_to_closer)
        response = "".join(str(chunk) for chunk in chunks)
        if response.strip():
            self._rendered_event = response
        return response

    def finish_streamed_response(self, answer: str) -> None:
        _ = answer

    def mark_turn_complete(self) -> None:
        """Mark that the real agent turn drove this sink."""
        self._completed_turn = True

    def clear_rendered_event(self) -> None:
        """Discard a prior outer turn's response before the next one starts."""
        self._rendered_event = ""

    @property
    def rendered_response(self) -> str:
        """Return only the response or error the interactive terminal would render."""
        return self._rendered_event.strip()

    @property
    def completed_turn(self) -> bool:
        """Whether this sink observed a completed real agent turn."""
        return self._completed_turn


@contextmanager
def _ask_log_scope() -> Iterator[None]:
    """Keep unrendered internal warnings out of a normal one-shot response."""
    root = logging.getLogger()
    if root.handlers:
        yield
        return

    # A normal CLI process does not configure root logging. Without a handler,
    # Python's ``lastResort`` handler writes warnings such as a failed shell
    # probe directly to stderr, ahead of the answer renderer. The tool result
    # remains in the agent context; the final response is the user-facing
    # diagnostic. Do not override an embedding host's configured logging.
    handler = logging.NullHandler()
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)


def _restrict_ask_capabilities(
    session: SessionCore,
    *,
    deferred_user_choices: bool = True,
) -> None:
    """Zero the capabilities the one-shot ask agent must not use."""
    for capability in _ASK_DISABLED_CAPABILITIES:
        session.available_capabilities[capability] = ()
    session.available_capabilities["ask_user_choice"] = (
        ("deferred",) if deferred_user_choices else ()
    )


def _resumed_turn_did_not_complete(
    result: TurnResult,
    *,
    potential_mutation_started: bool,
) -> bool:
    """Whether a consumed choice can safely be restored for a retry.

    Once potentially mutating work starts, retrying the choice could replay
    an external side effect; read-only tools do not create that risk.
    """
    if potential_mutation_started:
        return False
    action = result.action_result
    return action.accounting_status == "not_run" or (
        result.cancelled and action.executed_count == 0
    )


def _run_agent_turn(
    prompt: str,
    hooks: ToolExecutionHooks,
    *,
    tool_event_observer: ToolEventObserver | None = None,
    output: _AskOutputSink | None = None,
    session_id: str | None = None,
    ephemeral: bool = True,
    fresh_session_id: str | None = None,
    run_state: _AskRunState | None = None,
    context_files: tuple[AskFileInput, ...] = (),
) -> TurnResult:
    manager = SessionManager()
    output = output or _AskOutputSink()
    cancel_event = ensure_turn_cancel(output)
    console = _CancellableConsole(cancel_event)
    is_resumed_session = session_id is not None
    mutation_tracker = _ResumedMutationTracker() if is_resumed_session else None
    tracked_hooks = (
        _track_resumed_mutations(hooks, mutation_tracker) if mutation_tracker is not None else hooks
    )
    session: SessionCore | None = None
    try:
        with ask_signal_scope(cancel_event), _ask_log_scope():
            agent_session = AgentSession.start(
                SessionConfig(
                    session_id=session_id,
                    new_session_id=fresh_session_id,
                    load_env=True,
                    hydrate_integrations=True,
                    warm_integrations=True,
                    persistent_tasks=False,
                    open_store=not ephemeral,
                    session_manager=manager,
                ),
                output=output,
                prepare_session=partial(
                    _restrict_ask_capabilities,
                    deferred_user_choices=not ephemeral,
                ),
                console=console,
                surface=PromptSurface.HEADLESS_CLI.value,
                is_tty=False,
                tool_hooks=tracked_hooks,
                tool_event_observer=tool_event_observer,
            )
            session = agent_session.bound_session
            if session is None:
                raise RuntimeError("AgentSession.start() did not bind a session.")
            if run_state is not None:
                run_state.session_id = None if ephemeral else session.session_id
            prior_choice_state = (
                pending_user_choice_state_snapshot(session) if is_resumed_session else None
            )
            if context_files and getattr(session, "pending_user_choice", None) is not None:
                raise OpenSREError(
                    "Context files cannot be attached while answering a pending choice.",
                    suggestion=(
                        "Resume with the answer first, then attach context files in a follow-up."
                    ),
                )
            turn_prompt = _resume_prompt(session, prompt) if is_resumed_session else prompt
            turn_prompt = render_prompt_with_context(turn_prompt, context_files)
            restored_prior_choice = False
            try:
                result = agent_session.chat(turn_prompt)
            except AskSignal:
                # A failed resume must not consume its still-unhandled question.
                if prior_choice_state is not None and not (
                    mutation_tracker and mutation_tracker.potential_mutation_started
                ):
                    apply_pending_user_choice_state(session, prior_choice_state)
                raise
            except Exception:
                # A failed resume must not consume its still-unhandled question.
                if prior_choice_state is not None and not (
                    mutation_tracker and mutation_tracker.potential_mutation_started
                ):
                    apply_pending_user_choice_state(session, prior_choice_state)
                raise
            if (
                prior_choice_state is not None
                and _resumed_turn_did_not_complete(
                    result,
                    potential_mutation_started=bool(
                        mutation_tracker and mutation_tracker.potential_mutation_started
                    ),
                )
                and getattr(session, "pending_user_choice", None) is None
            ):
                # A no-op/retry result normally restores the question the
                # resume just consumed.  If the agent has already queued a
                # replacement question, that is newer state and must win.
                apply_pending_user_choice_state(session, prior_choice_state)
                restored_prior_choice = True
            output.mark_turn_complete()
            if run_state is not None:
                run_state.pending_choice = getattr(session, "pending_user_choice", None)
                run_state.pending_choice_is_new = (
                    run_state.pending_choice is not None and not restored_prior_choice
                )
            return result
    finally:
        if session is not None:
            manager.close(session, extract_memory=False)


def _outcome_questions(pending: PendingUserChoice) -> tuple[AskQuestion, ...]:
    items = pending.items()
    return tuple(
        AskQuestion(
            label=question.label,
            title=question.title,
            options=question.options,
            multi_select=question.multi_select,
            allow_custom=pending.custom_answer if len(items) == 1 else True,
        )
        for question in items
    )


def _questions_response(questions: tuple[AskQuestion, ...]) -> str:
    blocks: list[str] = []
    for question in questions:
        options = "\n".join(
            f"  {index}. {option}" for index, option in enumerate(question.options, start=1)
        )
        blocks.append(f"{question.title}\n{options}")
    return "\n\n".join(blocks)


def _successful_turn(result: TurnResult, response: str) -> bool:
    action = result.action_result
    action_ok = (
        action.handled
        and not action.has_unhandled_clause
        and not action.hit_iteration_cap
        and action.accounting_status == "completed"
    )
    return bool(
        action.accounting_status == "completed"
        and (result.answered or action_ok)
        and not action.hit_iteration_cap
        and not result.cancelled
        and response
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
    resume_session_id: str | None = None,
    ephemeral: bool = False,
    context_files: tuple[AskFileInput, ...] = (),
) -> AskOutcome:
    """Execute one ask turn with invocation-scoped approval authority."""
    if resume_session_id and ephemeral:
        return AskOutcome(
            status=AskStatus.ERROR,
            response="",
            error=AskError(message="A resumed session cannot be ephemeral."),
            exit_code=AskExitCode.ERROR,
        )
    tracker = ApprovalTracker()
    output = _AskOutputSink()
    hooks = build_approval_hooks(
        allowed_tools=allowed_tools,
        bypass_approvals=bypass_approvals,
        tracker=tracker,
    )
    run_state = _AskRunState()
    try:
        resume_id = _resolve_resume_session_id(resume_session_id) if resume_session_id else None
        fresh_session_id = None
        if resume_id is None and not ephemeral:
            # A persisted ask session is visible as soon as AgentSession.start
            # writes its header.  Allocate its ID before entering the shared
            # lease so a concurrent --resume cannot race the first turn.
            fresh_session_id = str(uuid4())
        with _ask_session_lock(resume_id or fresh_session_id):
            result = _run_agent_turn(
                prompt,
                hooks,
                tool_event_observer=tool_event_observer,
                output=output,
                session_id=resume_id,
                ephemeral=ephemeral,
                fresh_session_id=fresh_session_id,
                run_state=run_state,
                context_files=context_files,
            )
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

    # The shared turn result retains tool history for session persistence. A
    # one-shot CLI must instead print the same concise response the terminal
    # surface selected, never raw shell stdout/stderr or command transcripts.
    response = output.rendered_response if output.completed_turn else result.primary_response_text
    denied = _approval_denied_outcome(
        tracker,
        response=response,
    )
    if denied is not None:
        return denied
    if result.cancelled:
        return AskOutcome(
            status=AskStatus.CANCELLED,
            response=response or "Agent execution cancelled.",
            exit_code=AskExitCode.SIGINT,
        )
    if result.action_result.accounting_status == "not_run" and not (
        run_state.pending_choice is not None and run_state.pending_choice_is_new
    ):
        return AskOutcome(
            status=AskStatus.ERROR,
            response="",
            error=AskError(message=response or "The agent did not complete the request."),
            exit_code=AskExitCode.ERROR,
        )
    if run_state.pending_choice is not None:
        questions = _outcome_questions(run_state.pending_choice)
        return AskOutcome(
            status=AskStatus.NEEDS_INPUT,
            response=_questions_response(questions),
            session_id=run_state.session_id,
            questions=questions,
            exit_code=AskExitCode.NEEDS_INPUT,
        )
    if not _successful_turn(result, response):
        return AskOutcome(
            status=AskStatus.ERROR,
            response="",
            error=AskError(message=response or "The agent did not complete the request."),
            exit_code=AskExitCode.ERROR,
        )
    return AskOutcome(
        status=AskStatus.SUCCESS,
        response=response,
        session_id=run_state.session_id,
    )


__all__ = [
    "AskError",
    "AskExitCode",
    "AskOutcome",
    "AskQuestion",
    "AskSignal",
    "AskStatus",
    "ask_signal_scope",
    "cancelled_outcome",
    "run_ask",
]

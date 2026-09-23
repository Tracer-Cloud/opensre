"""Runs queued remote prompts one at a time through the gateway's turn runner.

A turn that needs the caller (a question, or a tool that requires approval) ends as
``needs_input``; the answer comes back as a follow-up job that resumes the session.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from contextlib import ExitStack
from typing import Any, Protocol

from config.constants.organization import organization_id
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness import SessionCore, TurnResult
from core.tool import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from gateway.core.billing.turn_metering import bound_turn_metering
from gateway.core.middleware.approvals import arguments_preview
from gateway.core.prompt_intake.jobs import PromptJob, PromptQueue
from gateway.core.prompt_intake.output import CollectingTurnOutput
from infrastructure.analytics.usage_context import UsageSurface, bound_usage_context
from infrastructure.turn_host.unattended_session import (
    AnswerRejected,
    UnattendedSessions,
    answer_pending_choice,
    approval_grant,
    approval_question,
    choice_view,
    invocation_key,
)
from tools.registry import integration_of_tool

ERROR_CREDITS_DENIED = "credits_denied"
ERROR_NOT_ADMITTED = "not_admitted"
ERROR_TURN_FAILED = "turn_failed"
ERROR_INVALID_ANSWER = "invalid_answer"

_APPROVAL_BLOCKED = (
    "This tool needs the user's approval. The turn ends now and resumes with their "
    "decision: do not retry it and do not call other tools; say in one sentence what "
    "you wanted to do and why."
)
_ALREADY_WAITING = (
    "The user is already being asked something; end the turn now and wait for the answer."
)

_POLL_SECONDS = 1.0


class PromptTurnRunner(Protocol):
    """The gateway's turn runner: ``None`` means the turn was not admitted."""

    def run(
        self,
        text: str,
        session: SessionCore,
        output: Any,
        logger: logging.Logger,
    ) -> TurnResult | None:
        """Run one turn and return its result, or ``None`` when a gate refused it."""

    def drop_session(self, session_id: str) -> None:
        """Release what the runner pooled for ``session_id``."""


class PromptWorker:
    """One thread: take a job, run the turn, settle the job, repeat until stopped."""

    def __init__(
        self,
        queue: PromptQueue,
        runner: PromptTurnRunner,
        *,
        logger: logging.Logger,
        sessions: UnattendedSessions | None = None,
    ) -> None:
        self._queue = queue
        self._runner = runner
        self._logger = logger
        self._sessions = sessions or UnattendedSessions()
        #: Exact invocations the caller approved, per session; each grant is used once.
        self._approved: dict[str, set[str]] = {}
        #: The question each session stopped on, until its answer resumes the session.
        self._asked: dict[str, Any] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="opensre-prompt-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, *, timeout_seconds: float) -> bool:
        """Ask the loop to end after its current job; return whether it did in time."""
        self._stop.set()
        self._thread.join(timeout=timeout_seconds)
        ended = not self._thread.is_alive()
        for session_id in list(self._asked):
            self._forget(session_id)
        return ended

    def run_one(self, job: PromptJob) -> None:
        """Run ``job`` to a settled state; never raises."""
        try:
            self._run_job(job)
        except Exception:
            self._logger.exception("remote prompt %s failed", job.id)
            self._queue.fail(job, ERROR_TURN_FAILED)

    def _run(self) -> None:
        while not self._stop.is_set():
            job = self._queue.take(timeout_seconds=_POLL_SECONDS)
            if job is not None:
                self.run_one(job)
            self.retire_forgotten()

    def retire_forgotten(self) -> None:
        """Release a session once the queue holds none of its prompts any more.

        A follow-up may still be asking on the session its expired parent opened;
        the session stays until that newer prompt is forgotten too.
        """
        for forgotten in self._queue.take_forgotten():
            session_id = forgotten.session_id
            if session_id in self._asked and not self._queue.holds_session(session_id):
                self._forget(session_id)

    def _run_job(self, job: PromptJob) -> None:
        if job.parent_id:
            session = self._sessions.resume(job.session_id)
            session.pending_user_choice = self._asked.pop(session.session_id, None)
            text = self._answer_text(job, session)
            if text is None:
                self._sessions.close(session)
                return
        else:
            session = self._sessions.open()
            job.session_id = session.session_id
            text = _render_prompt(job)
        output = CollectingTurnOutput(on_status=self._progress_writer(job))
        failures = _IntegrationFailures()
        approvals = _Approvals(session, self._approved.get(session.session_id, set()))
        output.tool_hooks = ToolExecutionHooks(
            before_tool_call=approvals.before_tool_call,
            after_tool_call=failures.after_tool_call,
        )
        denial = _Denial()
        org = organization_id()
        try:
            with _turn_context(org, job, session, denial):
                result = self._runner.run(text, session, output, self._logger)
        finally:
            self._sessions.close(session)

        failed = failures.vendors()
        pending = getattr(session, "pending_user_choice", None)
        if pending is not None:
            self._asked[session.session_id] = pending
            self._queue.needs_input(
                job,
                _question_text(pending),
                choice=choice_view(pending),
                failed_integrations=failed,
            )
            return
        self._forget(session.session_id)
        if denial.credits_denied:
            self._queue.fail(job, ERROR_CREDITS_DENIED, failed_integrations=failed)
            return
        if result is None:
            self._queue.fail(job, ERROR_NOT_ADMITTED, failed_integrations=failed)
            return
        if output.failed:
            self._queue.fail(job, ERROR_TURN_FAILED, failed_integrations=failed)
            return
        self._queue.finish(job, output.answer, failed_integrations=failed)

    def _progress_writer(self, job: PromptJob) -> Callable[[str], None]:
        """A callback that records one status line on ``job``."""

        def note(text: str) -> None:
            self._queue.note(job, text)

        return note

    def _answer_text(self, job: PromptJob, session: SessionCore) -> str | None:
        """The resumed turn's user message; ``None`` after settling an answer that did not fit."""
        pending = session.pending_user_choice
        granted = approval_grant(pending, job.prompt)
        try:
            text = answer_pending_choice(session, job.prompt)
        except AnswerRejected:
            self._asked[session.session_id] = pending
            self._queue.reopen(job.parent_id)
            self._queue.fail(job, ERROR_INVALID_ANSWER)
            return None
        if granted is not None:
            self._approved.setdefault(session.session_id, set()).add(granted)
        # The answered question must not come back from the store during the turn.
        self._sessions.flush(session)
        return text

    def _forget(self, session_id: str) -> None:
        """The session is done with: release the pooled agent, the question and the grants."""
        self._approved.pop(session_id, None)
        self._asked.pop(session_id, None)
        self._runner.drop_session(session_id)


class _Approvals:
    """Turns ``requires_approval`` into an Approve/Deny question the caller answers later."""

    def __init__(self, session: SessionCore, approved: set[str]) -> None:
        self._session = session
        self._approved = approved

    def before_tool_call(self, request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        tool = request.tool
        if not bool(getattr(tool, "requires_approval", False)):
            return None
        name = request.tool_call.name
        key = invocation_key(name, request.arguments)
        if key in self._approved:
            # One grant covers exactly this call, once.
            self._approved.discard(key)
            return None
        if self._session.pending_user_choice is not None:
            return BeforeToolCallResult(blocked=True, terminate=True, reason=_ALREADY_WAITING)
        reason = str(getattr(tool, "approval_reason", "") or "")
        preview = arguments_preview(request.arguments)
        self._session.pending_user_choice = approval_question(
            name, request.arguments, reason, preview
        )
        return BeforeToolCallResult(blocked=True, terminate=True, reason=_APPROVAL_BLOCKED)


class _IntegrationFailures:
    """Collects the integrations whose tools returned an error during the turn."""

    def __init__(self) -> None:
        # Insertion-ordered set: first failure decides the reporting order.
        self._vendors: dict[str, None] = {}

    def after_tool_call(self, request: ToolExecutionRequest, result: ToolExecutionResult) -> None:
        if not result.is_error:
            return None
        vendor = integration_of_tool(request.tool_call.name)
        if vendor is not None:
            self._vendors.setdefault(vendor, None)
        return None

    def vendors(self) -> tuple[str, ...]:
        return tuple(self._vendors)


class _Denial:
    """Set by metering when the organization has no credits for this turn."""

    def __init__(self) -> None:
        self.credits_denied = False

    def __call__(self) -> None:
        self.credits_denied = True


def _turn_context(org: str, job: PromptJob, session: SessionCore, denial: _Denial) -> ExitStack:
    """Storage scope, usage attribution and metering for one remote turn."""
    stack = ExitStack()
    if org:
        scope = StorageScope(principal=Principal.org(org), actor=Actor(id=job.actor))
        stack.enter_context(bound_storage_scope(scope))
    stack.enter_context(
        bound_usage_context(
            surface=UsageSurface.PROMPT.value,
            session_id=session.session_id,
            user_id=job.actor,
            organization_id=org or None,
        )
    )
    stack.enter_context(
        bound_turn_metering(
            organization_id=org,
            reason="prompt_turn",
            idempotency_key=f"{UsageSurface.PROMPT.value}:{job.id}",
            on_denied=denial,
        )
    )
    return stack


def _render_prompt(job: PromptJob) -> str:
    """The prompt plus the facts the caller resolved up front, so nothing is left to ask."""
    if not job.context:
        return job.prompt
    facts = "\n".join(f"- {key}: {value}" for key, value in sorted(job.context.items()))
    return f"{job.prompt}\n\nKnown context:\n{facts}"


def _question_text(pending: Any) -> str:
    """The pending choice as plain text: the header, then each question with its options."""
    lines = [str(getattr(pending, "title", "") or "The agent needs an answer.")]
    note = str(getattr(pending, "note", "") or "")
    if note:
        lines.append(note)
    questions = getattr(pending, "questions", ()) or ()
    options = getattr(pending, "options", ()) or ()
    if questions:
        for question in questions:
            question_options = ", ".join(getattr(question, "options", ()) or ())
            title = getattr(question, "title", "")
            suffix = f" ({question_options})" if question_options else ""
            lines.append(f"- {title}{suffix}")
    elif options:
        lines.append("Options: " + ", ".join(options))
    return "\n".join(lines)


__all__ = [
    "ERROR_CREDITS_DENIED",
    "ERROR_INVALID_ANSWER",
    "ERROR_NOT_ADMITTED",
    "ERROR_TURN_FAILED",
    "PromptTurnRunner",
    "PromptWorker",
]

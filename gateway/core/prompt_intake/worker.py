"""Runs queued remote prompts one at a time through the gateway's turn runner."""

from __future__ import annotations

import logging
import threading
from contextlib import ExitStack
from typing import Any, Protocol

from config.constants.organization import organization_id
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness import SessionCore, TurnResult
from gateway.core.billing.turn_metering import bound_turn_metering
from gateway.core.prompt_intake.jobs import PromptJob, PromptQueue
from gateway.core.prompt_intake.output import CollectingTurnOutput
from infrastructure.analytics.usage_context import UsageSurface, bound_usage_context
from infrastructure.turn_host.unattended_session import UnattendedSessions

ERROR_CREDITS_DENIED = "credits_denied"
ERROR_NOT_ADMITTED = "not_admitted"
ERROR_TURN_FAILED = "turn_failed"

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
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="opensre-prompt-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, *, timeout_seconds: float) -> bool:
        """Ask the loop to end after its current job; return whether it did in time."""
        self._stop.set()
        self._thread.join(timeout=timeout_seconds)
        ended = not self._thread.is_alive()
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

    def _run_job(self, job: PromptJob) -> None:
        session = self._sessions.open()
        job.session_id = session.session_id
        output = CollectingTurnOutput()
        denial = _Denial()
        org = organization_id()
        try:
            with _turn_context(org, job, session, denial):
                result = self._runner.run(_render_prompt(job), session, output, self._logger)
        finally:
            self._sessions.close(session)

        pending = getattr(session, "pending_user_choice", None)
        if pending is not None:
            self._queue.needs_input(job, _question_text(pending))
            return
        if denial.credits_denied:
            self._queue.fail(job, ERROR_CREDITS_DENIED)
            return
        if result is None:
            self._queue.fail(job, ERROR_NOT_ADMITTED)
            return
        if output.failed:
            self._queue.fail(job, ERROR_TURN_FAILED)
            return
        self._queue.finish(job, output.answer)


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
    "ERROR_NOT_ADMITTED",
    "ERROR_TURN_FAILED",
    "PromptTurnRunner",
    "PromptWorker",
]

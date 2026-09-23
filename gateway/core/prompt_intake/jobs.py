"""Prompt jobs: what a remote caller submitted and what became of it."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from config.constants.gateway import (
    PROMPT_PROGRESS_LINE_MAX_CHARS,
    PROMPT_PROGRESS_MAX_LINES,
    PROMPT_QUEUE_MAX,
    PROMPT_RESULT_RETENTION_SECONDS,
)


class AnswerRefused(Exception):
    """The prompt cannot take an answer; ``code`` says why (``not_waiting``, ``already_answered``)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


NOT_WAITING = "not_waiting"
ALREADY_ANSWERED = "already_answered"


class PromptState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


_SETTLED = frozenset({PromptState.DONE, PromptState.NEEDS_INPUT, PromptState.FAILED})


@dataclass
class PromptJob:
    """One submitted prompt; mutated only through :class:`PromptQueue`."""

    id: str
    prompt: str
    context: dict[str, str]
    actor: str
    submitted_at: float
    state: PromptState = PromptState.QUEUED
    answer: str = ""
    question: str = ""
    error_code: str = ""
    finished_at: float | None = None
    session_id: str = ""
    #: Integrations whose tools failed during the turn, by vendor name (e.g. ``github``).
    failed_integrations: tuple[str, ...] = ()
    #: The pending choice as menu data, set with ``needs_input``.
    choice: dict[str, Any] | None = None
    #: For a follow-up: the prompt whose question this job answers.
    parent_id: str = ""
    #: For a prompt that asked: the follow-up job carrying the answer.
    answered_by: str = ""
    #: Newest progress lines as ``(index, text)``; the index lets a poller print each once.
    progress: deque[tuple[int, str]] = field(
        default_factory=lambda: deque(maxlen=PROMPT_PROGRESS_MAX_LINES), repr=False
    )
    progress_count: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def settled(self) -> bool:
        return self.state in _SETTLED

    def view(self) -> dict[str, Any]:
        """The caller-facing record: stable codes and text, never internals."""
        with self._lock:
            record: dict[str, Any] = {"prompt_id": self.id, "state": self.state.value}
            if self.state is PromptState.DONE:
                record["answer"] = self.answer
            if self.state is PromptState.NEEDS_INPUT:
                record["question"] = self.question
                if self.choice is not None:
                    record["choice"] = self.choice
            if self.state is PromptState.FAILED:
                record["error"] = self.error_code
            if self.finished_at is not None:
                record["finished_at"] = self.finished_at
            if self.failed_integrations:
                record["failed_integrations"] = list(self.failed_integrations)
            if self.progress:
                record["progress"] = [
                    {"index": index, "text": text} for index, text in self.progress
                ]
            return record


class PromptQueue:
    """Bounded FIFO of prompts plus their results, kept for a retention window."""

    def __init__(
        self,
        *,
        max_queued: int = PROMPT_QUEUE_MAX,
        retention_seconds: float = PROMPT_RESULT_RETENTION_SECONDS,
        clock: Any = time.time,
    ) -> None:
        self._max_queued = max_queued
        self._retention_seconds = retention_seconds
        self._clock = clock
        self._pending: deque[PromptJob] = deque()
        self._jobs: dict[str, PromptJob] = {}
        #: Settled jobs dropped by retention, kept until the worker retires their sessions.
        self._forgotten: deque[PromptJob] = deque()
        self._lock = threading.Lock()
        self._available = threading.Condition(self._lock)

    def submit(self, prompt: str, *, context: dict[str, str], actor: str) -> PromptJob | None:
        """Queue a prompt; ``None`` when the queue is full."""
        with self._lock:
            self._forget_expired()
            if len(self._pending) >= self._max_queued:
                return None
            job = PromptJob(
                id=f"p_{uuid.uuid4().hex}",
                prompt=prompt,
                context=dict(context),
                actor=actor,
                submitted_at=self._clock(),
            )
            self._pending.append(job)
            self._jobs[job.id] = job
            self._available.notify()
            return job

    def answer(self, parent: PromptJob, answer: str) -> PromptJob | None:
        """Queue the answer as a follow-up on the parent's session; ``None`` when full.

        Raises :class:`AnswerRefused` when the parent is not waiting for an answer
        or already has one.
        """
        with self._lock:
            self._forget_expired()
            with parent._lock:
                if parent.state is not PromptState.NEEDS_INPUT:
                    raise AnswerRefused(NOT_WAITING)
                if parent.answered_by:
                    raise AnswerRefused(ALREADY_ANSWERED)
                if len(self._pending) >= self._max_queued:
                    return None
                job = PromptJob(
                    id=f"p_{uuid.uuid4().hex}",
                    prompt=answer,
                    context={},
                    actor=parent.actor,
                    submitted_at=self._clock(),
                    session_id=parent.session_id,
                    parent_id=parent.id,
                )
                parent.answered_by = job.id
            self._pending.append(job)
            self._jobs[job.id] = job
            self._available.notify()
            return job

    def reopen(self, parent_id: str) -> None:
        """Let the parent take another answer after a follow-up could not use its answer."""
        with self._lock:
            parent = self._jobs.get(parent_id)
            if parent is None:
                return
            with parent._lock:
                parent.answered_by = ""

    def take(self, *, timeout_seconds: float) -> PromptJob | None:
        """Block for the next queued job, marking it running; ``None`` on timeout."""
        with self._lock:
            if not self._pending:
                self._available.wait(timeout=timeout_seconds)
            if not self._pending:
                return None
            job = self._pending.popleft()
            job.state = PromptState.RUNNING
            return job

    def get(self, prompt_id: str) -> PromptJob | None:
        with self._lock:
            self._forget_expired()
            return self._jobs.get(prompt_id)

    def finish(
        self, job: PromptJob, answer: str, *, failed_integrations: tuple[str, ...] = ()
    ) -> None:
        self._settle(job, PromptState.DONE, answer=answer, failed_integrations=failed_integrations)

    def needs_input(
        self,
        job: PromptJob,
        question: str,
        *,
        choice: dict[str, Any] | None = None,
        failed_integrations: tuple[str, ...] = (),
    ) -> None:
        self._settle(
            job,
            PromptState.NEEDS_INPUT,
            question=question,
            choice=choice,
            failed_integrations=failed_integrations,
        )

    def fail(
        self, job: PromptJob, error_code: str, *, failed_integrations: tuple[str, ...] = ()
    ) -> None:
        self._settle(
            job, PromptState.FAILED, error_code=error_code, failed_integrations=failed_integrations
        )

    def note(self, job: PromptJob, text: str) -> None:
        """Append one progress line to the running job; older lines fall off the end."""
        line = text.strip()[:PROMPT_PROGRESS_LINE_MAX_CHARS]
        if not line:
            return
        with job._lock:
            job.progress.append((job.progress_count, line))
            job.progress_count += 1

    def take_forgotten(self) -> list[PromptJob]:
        """Jobs dropped by retention since the last call, so their sessions can be retired."""
        with self._lock:
            self._forget_expired()
            forgotten = list(self._forgotten)
            self._forgotten.clear()
            return forgotten

    def holds_session(self, session_id: str) -> bool:
        """Whether any retained job, settled or not, still belongs to ``session_id``."""
        with self._lock:
            return any(job.session_id == session_id for job in self._jobs.values())

    def queued_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def _settle(
        self,
        job: PromptJob,
        state: PromptState,
        *,
        answer: str = "",
        question: str = "",
        choice: dict[str, Any] | None = None,
        error_code: str = "",
        failed_integrations: tuple[str, ...] = (),
    ) -> None:
        with job._lock:
            job.state = state
            job.answer = answer
            job.question = question
            job.choice = choice
            job.error_code = error_code
            job.failed_integrations = failed_integrations
            job.finished_at = self._clock()

    def _forget_expired(self) -> None:
        """Drop settled results older than the retention window; caller holds the lock."""
        cutoff = self._clock() - self._retention_seconds
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job.settled and job.finished_at is not None and job.finished_at < cutoff
        ]
        for job_id in expired:
            self._forgotten.append(self._jobs.pop(job_id))


__all__ = [
    "ALREADY_ANSWERED",
    "NOT_WAITING",
    "AnswerRefused",
    "PromptJob",
    "PromptQueue",
    "PromptState",
]

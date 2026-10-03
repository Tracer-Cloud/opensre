"""Prompt jobs: what a remote caller submitted and what became of it."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeGuard

from config.constants.gateway import (
    PROMPT_PROGRESS_KIND_NOTE,
    PROMPT_PROGRESS_KIND_PLAN,
    PROMPT_PROGRESS_KIND_PLAN_DONE,
    PROMPT_PROGRESS_KINDS,
    PROMPT_PROGRESS_LINE_MAX_CHARS,
    PROMPT_PROGRESS_MAX_LINES,
    PROMPT_PROGRESS_PLAN_MAX_CHARS,
    PROMPT_PROGRESS_PLAN_OMITTED,
    PROMPT_QUEUE_MAX,
    PROMPT_RESULT_RETENTION_SECONDS,
)
from gateway.core.prompt_intake.job_store import PromptJobStore

_PLAN_PROGRESS_KINDS = frozenset({PROMPT_PROGRESS_KIND_PLAN, PROMPT_PROGRESS_KIND_PLAN_DONE})


def _bounded_progress_text(text: str, *, kind: str) -> str:
    """Cap one progress update without cutting a checklist step in half."""
    stripped = text.strip()
    if kind not in _PLAN_PROGRESS_KINDS:
        return stripped[:PROMPT_PROGRESS_LINE_MAX_CHARS]
    if len(stripped) <= PROMPT_PROGRESS_PLAN_MAX_CHARS:
        return stripped
    marker = PROMPT_PROGRESS_PLAN_OMITTED
    budget = PROMPT_PROGRESS_PLAN_MAX_CHARS - len(marker) - 1
    kept: list[str] = []
    used = 0
    for raw in stripped.splitlines():
        line = raw.rstrip()
        extra = len(line) + (1 if kept else 0)
        if used + extra > budget:
            break
        kept.append(line)
        used += extra
    if not kept:
        return marker
    kept.append(marker)
    return "\n".join(kept)


class AnswerRefused(Exception):
    """The prompt cannot take an answer; ``code`` says why (``not_waiting``, ``already_answered``)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


NOT_WAITING = "not_waiting"
ALREADY_ANSWERED = "already_answered"

#: Why a prompt failed, as the caller reads it in ``error``.
ERROR_CREDITS_DENIED = "credits_denied"
ERROR_NOT_ADMITTED = "not_admitted"
ERROR_TURN_FAILED = "turn_failed"
ERROR_INVALID_ANSWER = "invalid_answer"
#: The gateway task was replaced while the prompt was queued or running; it never finished.
ERROR_INTERRUPTED = "interrupted"

#: Shape of a persisted record; a record with another version is not read back.
_RECORD_VERSION = 1
_RECORD_TEXT_FIELDS = (
    "id",
    "prompt",
    "actor",
    "answer",
    "question",
    "error_code",
    "session_id",
    "parent_id",
    "answered_by",
)


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
    #: Advanced on every persisted change; the newest record of a prompt wins on reload.
    revision: int = 0
    #: Newest progress lines as ``(index, text, kind)``. The index lets a poller
    #: print each once; ``kind`` tells the shell how to paint the line.
    progress: deque[tuple[int, str, str]] = field(
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
            if self.parent_id:
                record["parent_prompt_id"] = self.parent_id
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
                    {"index": index, "text": text, "kind": kind}
                    for index, text, kind in self.progress
                ]
            return record

    def _bump(self) -> dict[str, Any]:
        """Advance the revision and return the durable record; the caller holds ``_lock``.

        Progress lines are left out: they only matter while a poller watches the turn.
        """
        self.revision += 1
        return {
            "v": _RECORD_VERSION,
            "revision": self.revision,
            **{name: getattr(self, name) for name in _RECORD_TEXT_FIELDS},
            "context": dict(self.context),
            "submitted_at": self.submitted_at,
            "state": self.state.value,
            "finished_at": self.finished_at,
            "failed_integrations": list(self.failed_integrations),
            "choice": self.choice,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> PromptJob | None:
        """Rebuild a job from a persisted record; ``None`` when this version cannot read it."""
        if record.get("v") != _RECORD_VERSION:
            return None
        texts = {name: record.get(name, "") for name in _RECORD_TEXT_FIELDS}
        if not all(isinstance(value, str) for value in texts.values()) or not texts["id"]:
            return None
        context = record.get("context")
        failed = record.get("failed_integrations")
        choice = record.get("choice")
        submitted_at = record.get("submitted_at")
        finished_at = record.get("finished_at")
        revision = record.get("revision")
        if not (
            isinstance(context, dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in context.items())
            and isinstance(failed, list)
            and all(isinstance(vendor, str) for vendor in failed)
            and (choice is None or isinstance(choice, dict))
            and _is_number(submitted_at)
            and (finished_at is None or _is_number(finished_at))
            and isinstance(revision, int)
        ):
            return None
        try:
            state = PromptState(str(record.get("state", "")))
        except ValueError:
            return None
        return cls(
            **texts,
            context=context,
            submitted_at=float(submitted_at),
            state=state,
            finished_at=None if finished_at is None else float(finished_at),
            failed_integrations=tuple(failed),
            choice=choice,
            revision=revision,
        )


class PromptQueue:
    """Bounded FIFO of prompts plus their results, kept for a retention window.

    With a ``store``, every state change is saved there and the prompts an
    earlier process left behind are taken back at construction.
    """

    def __init__(
        self,
        *,
        max_queued: int = PROMPT_QUEUE_MAX,
        retention_seconds: float = PROMPT_RESULT_RETENTION_SECONDS,
        clock: Any = time.time,
        store: PromptJobStore | None = None,
    ) -> None:
        self._max_queued = max_queued
        self._retention_seconds = retention_seconds
        self._clock = clock
        self._store = store
        self._pending: deque[PromptJob] = deque()
        self._jobs: dict[str, PromptJob] = {}
        #: Settled jobs dropped by retention, kept until the worker retires their sessions.
        self._forgotten: deque[PromptJob] = deque()
        self._lock = threading.Lock()
        self._available = threading.Condition(self._lock)
        if store is not None:
            self._restore(store)

    def _restore(self, store: PromptJobStore) -> None:
        """Take back the prompts an earlier process accepted.

        Settled prompts inside the retention window return as they were, so a
        question can still be read and answered. A prompt still queued or
        running died with that process: it settles as ``interrupted``, and a
        question whose answer it carried takes an answer again.
        """
        now = self._clock()
        cutoff = now - self._retention_seconds
        drop: list[str] = []
        interrupted: set[str] = set()
        for record in store.load():
            job = PromptJob.from_record(record)
            if job is None:
                drop.append(str(record.get("id", "")))
                continue
            if not job.settled:
                job.state = PromptState.FAILED
                job.error_code = ERROR_INTERRUPTED
                job.finished_at = now
                interrupted.add(job.id)
            elif job.finished_at is None or job.finished_at < cutoff:
                drop.append(job.id)
                continue
            self._jobs[job.id] = job
        reopened = [
            job.id
            for job in self._jobs.values()
            if job.answered_by
            and (job.answered_by in interrupted or job.answered_by not in self._jobs)
        ]
        for job_id in reopened:
            self._jobs[job_id].answered_by = ""
        store.compact(drop=drop)
        for job_id in (*interrupted, *reopened):
            job = self._jobs[job_id]
            with job._lock:
                store.save(job._bump())

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
            with job._lock:
                record = job._bump()
            self._pending.append(job)
            self._jobs[job.id] = job
            self._available.notify()
        self._save(record)
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
                with job._lock:
                    follow_up_record = job._bump()
                parent.answered_by = job.id
                parent_record = parent._bump()
            self._pending.append(job)
            self._jobs[job.id] = job
            self._available.notify()
        # The follow-up first: a parent saved as answered by a prompt the store never
        # got would refuse every later answer after a restart.
        self._save(follow_up_record, parent_record)
        return job

    def reopen(self, parent_id: str) -> None:
        """Let the parent take another answer after a follow-up could not use its answer."""
        with self._lock:
            parent = self._jobs.get(parent_id)
            if parent is None:
                return
            with parent._lock:
                parent.answered_by = ""
                record = parent._bump()
        self._save(record)

    def take(self, *, timeout_seconds: float) -> PromptJob | None:
        """Block for the next queued job, marking it running; ``None`` on timeout."""
        with self._lock:
            if not self._pending:
                self._available.wait(timeout=timeout_seconds)
            if not self._pending:
                return None
            job = self._pending.popleft()
            with job._lock:
                job.state = PromptState.RUNNING
                record = job._bump()
        self._save(record)
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

    def note(self, job: PromptJob, text: str, *, kind: str = PROMPT_PROGRESS_KIND_NOTE) -> None:
        """Append one progress line to the running job; older lines fall off the end.

        An identical ``plan`` line already at the tail is not recorded again:
        the shell replaces that checklist in place, so a repeat is noise.
        """
        if kind not in PROMPT_PROGRESS_KINDS:
            kind = PROMPT_PROGRESS_KIND_NOTE
        line = _bounded_progress_text(text, kind=kind)
        if not line:
            return
        with job._lock:
            if (
                kind == PROMPT_PROGRESS_KIND_PLAN
                and job.progress
                and job.progress[-1][1] == line
                and job.progress[-1][2] == kind
            ):
                return
            job.progress.append((job.progress_count, line, kind))
            job.progress_count += 1

    def take_forgotten(self) -> list[PromptJob]:
        """Jobs dropped by retention since the last call, so their sessions can be retired.

        Their records leave the store here too, off the request path.
        """
        with self._lock:
            self._forget_expired()
            forgotten = list(self._forgotten)
            self._forgotten.clear()
        if forgotten and self._store is not None:
            self._store.compact(drop=[job.id for job in forgotten])
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
            record = job._bump()
        self._save(record)

    def _save(self, *records: dict[str, Any]) -> None:
        """Hand changed records to the store; called without the queue lock held."""
        if self._store is None:
            return
        for record in records:
            self._store.save(record)

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


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


__all__ = [
    "ALREADY_ANSWERED",
    "ERROR_CREDITS_DENIED",
    "ERROR_INTERRUPTED",
    "ERROR_INVALID_ANSWER",
    "ERROR_NOT_ADMITTED",
    "ERROR_TURN_FAILED",
    "NOT_WAITING",
    "AnswerRefused",
    "PromptJob",
    "PromptQueue",
    "PromptState",
]

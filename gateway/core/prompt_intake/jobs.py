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
    PROMPT_FOREIGN_REFRESH_SECONDS,
    PROMPT_JOB_STALE_SECONDS,
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


class PromptNotSaved(Exception):
    """The store did not take a new prompt or answer, so it was not accepted."""


NOT_WAITING = "not_waiting"
ALREADY_ANSWERED = "already_answered"

#: Why a prompt failed, as the caller reads it in ``error``.
ERROR_CREDITS_DENIED = "credits_denied"
ERROR_NOT_ADMITTED = "not_admitted"
ERROR_TURN_FAILED = "turn_failed"
ERROR_INVALID_ANSWER = "invalid_answer"
#: The gateway task running the prompt stopped before it finished; it never will.
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
#: What another task's newer record may change on a job this task holds.
_ADOPTED_FIELDS = (
    "state",
    "answer",
    "question",
    "error_code",
    "finished_at",
    "session_id",
    "failed_integrations",
    "choice",
    "answered_by",
    "heartbeat_at",
    "revision",
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
    #: When the task that owns the job last saved it; how another task tells it is alive.
    heartbeat_at: float | None = None
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

    def _bump(self, now: float) -> dict[str, Any]:
        """Advance the revision, stamp the heartbeat and return the durable record.

        The caller holds ``_lock``. Progress lines are left out: they only
        matter while a poller watches the turn.
        """
        self.revision += 1
        self.heartbeat_at = now
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
            "heartbeat_at": self.heartbeat_at,
        }

    def _adopt(self, newer: PromptJob) -> None:
        """Take another task's later state of this job; the caller holds ``_lock``."""
        if newer.revision <= self.revision:
            return
        for name in _ADOPTED_FIELDS:
            setattr(self, name, getattr(newer, name))

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
        heartbeat_at = record.get("heartbeat_at")
        revision = record.get("revision")
        if not (
            isinstance(context, dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in context.items())
            and isinstance(failed, list)
            and all(isinstance(vendor, str) for vendor in failed)
            and (choice is None or isinstance(choice, dict))
            and _is_number(submitted_at)
            and (finished_at is None or _is_number(finished_at))
            and (heartbeat_at is None or _is_number(heartbeat_at))
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
            heartbeat_at=None if heartbeat_at is None else float(heartbeat_at),
        )


class PromptQueue:
    """Bounded FIFO of prompts plus their results, kept for a retention window.

    With a ``store``, a prompt or an answer is accepted only once the store has
    it; later changes are saved there too, and the prompts an earlier task left
    behind are taken back at construction. An unsettled prompt whose owner is
    still alive (it saved the prompt within ``stale_seconds``) stays that
    task's: it is not run here, only re-read, until it settles or its owner
    falls silent and it becomes ``interrupted``.
    """

    def __init__(
        self,
        *,
        max_queued: int = PROMPT_QUEUE_MAX,
        retention_seconds: float = PROMPT_RESULT_RETENTION_SECONDS,
        clock: Any = time.time,
        store: PromptJobStore | None = None,
        stale_seconds: float = PROMPT_JOB_STALE_SECONDS,
        refresh_seconds: float = PROMPT_FOREIGN_REFRESH_SECONDS,
    ) -> None:
        self._max_queued = max_queued
        self._retention_seconds = retention_seconds
        self._clock = clock
        self._store = store
        self._stale_seconds = stale_seconds
        self._refresh_seconds = refresh_seconds
        self._pending: deque[PromptJob] = deque()
        self._jobs: dict[str, PromptJob] = {}
        #: Queue slots held by a prompt or answer whose record is still being saved.
        self._reserved = 0
        #: Unsettled prompts another live task owns; re-read, never run here.
        self._foreign: set[str] = set()
        self._foreign_read_at: float | None = None
        #: When each waiting question was last re-read for an answer another task took.
        self._reread_at: dict[str, float] = {}
        #: Settled jobs dropped by retention, kept until the worker retires their sessions.
        self._forgotten: deque[PromptJob] = deque()
        self._lock = threading.Lock()
        self._available = threading.Condition(self._lock)
        if store is not None:
            self._restore(store)

    def _restore(self, store: PromptJobStore) -> None:
        """Take back the prompts an earlier task accepted.

        Settled prompts inside the retention window return as they were, so a
        question can still be read and answered. An unsettled prompt is left to
        its owner while that task is alive and is ``interrupted`` otherwise.
        """
        now = self._clock()
        drop: list[str] = []
        for record in store.load():
            job = PromptJob.from_record(record)
            if job is None:
                drop.append(str(record.get("id", "")))
                continue
            self._jobs[job.id] = job
        changed: dict[str, PromptJob] = {}
        for job in list(self._jobs.values()):
            if job.settled:
                continue
            if self._alive(job, now):
                self._foreign.add(job.id)
            else:
                changed.update((taken.id, taken) for taken in self._take_over(job, now))
        for job in self._jobs.values():
            if job.answered_by and job.answered_by not in self._jobs:
                # The answer's record never reached the store; the question takes one again.
                self._reopen(job, now)
                changed[job.id] = job
        for job_id in self._expired_ids(now):
            del self._jobs[job_id]
            changed.pop(job_id, None)
            drop.append(job_id)
        store.compact(drop=drop)
        records: list[dict[str, Any]] = []
        for job in changed.values():
            with job._lock:
                records.append(job._bump(now))
        self._save(*records)

    def submit(self, prompt: str, *, context: dict[str, str], actor: str) -> PromptJob | None:
        """Queue a prompt; ``None`` when the queue is full.

        Raises :class:`PromptNotSaved` when the store did not take it: a prompt
        is acknowledged only once a replacement task could still answer it.
        """
        now = self._clock()
        with self._lock:
            self._forget_expired()
            if len(self._pending) + self._reserved >= self._max_queued:
                return None
            job = PromptJob(
                id=f"p_{uuid.uuid4().hex}",
                prompt=prompt,
                context=dict(context),
                actor=actor,
                submitted_at=now,
            )
            with job._lock:
                record = job._bump(now)
            self._reserved += 1
        saved = self._save(record)
        with self._lock:
            self._reserved -= 1
            if not saved:
                raise PromptNotSaved
            self._enqueue(job)
        return job

    def answer(self, parent: PromptJob, answer: str) -> PromptJob | None:
        """Queue the answer as a follow-up on the parent's session; ``None`` when full.

        Raises :class:`AnswerRefused` when the parent is not waiting for an answer
        or already has one — here or, per the store, in another task — and
        :class:`PromptNotSaved` when the store did not take the answer (the parent
        then takes an answer again).
        """
        self._refresh_foreign()
        now = self._clock()
        with self._lock:
            self._forget_expired()
            with parent._lock:
                if parent.state is not PromptState.NEEDS_INPUT:
                    raise AnswerRefused(NOT_WAITING)
                if parent.answered_by:
                    raise AnswerRefused(ALREADY_ANSWERED)
                if len(self._pending) + self._reserved >= self._max_queued:
                    return None
                job = PromptJob(
                    id=f"p_{uuid.uuid4().hex}",
                    prompt=answer,
                    context={},
                    actor=parent.actor,
                    submitted_at=now,
                    session_id=parent.session_id,
                    parent_id=parent.id,
                )
                known_revision = parent.revision
                # Holds the question against a second answer in this task while the claim runs.
                parent.answered_by = job.id
            self._reserved += 1
        try:
            claimed = self._claim(parent, job, known_revision, now)
        except AnswerRefused:
            self._release(parent, job)
            # Another task took the question: show its claim here from now on.
            self._reread(parent)
            raise
        if not claimed:
            self._release(parent, job)
            raise PromptNotSaved
        with self._lock:
            self._reserved -= 1
            self._enqueue(job)
        return job

    def reopen(self, parent_id: str) -> None:
        """Let the parent take another answer after a follow-up could not use its answer."""
        now = self._clock()
        with self._lock:
            parent = self._jobs.get(parent_id)
            if parent is None:
                return
            self._reopen(parent, now)
            with parent._lock:
                record = parent._bump(now)
        # Like every change after acceptance, a failed save is logged and the job goes on.
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
                record = job._bump(self._clock())
        self._save(record)
        return job

    def get(self, prompt_id: str) -> PromptJob | None:
        self._refresh_foreign()
        self._reread_waiting(prompt_id)
        with self._lock:
            self._forget_expired()
            return self._jobs.get(prompt_id)

    def heartbeat(self) -> None:
        """Re-save the unsettled prompts this task owns, then re-read the ones it does not.

        Called periodically while the worker runs, so a task starting beside this
        one sees these prompts are alive and leaves them alone.
        """
        now = self._clock()
        records: list[dict[str, Any]] = []
        with self._lock:
            for job in self._jobs.values():
                if job.id in self._foreign:
                    continue
                with job._lock:
                    if not job.settled:
                        records.append(job._bump(now))
        self._save(*records)
        self._refresh_foreign()

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
        self._refresh_foreign()
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
        now = self._clock()
        with job._lock:
            job.state = state
            job.answer = answer
            job.question = question
            job.choice = choice
            job.error_code = error_code
            job.failed_integrations = failed_integrations
            job.finished_at = now
            record = job._bump(now)
        # The prompt is already accepted: a failed save is logged and the caller still
        # reads the outcome from memory; only a restart before the next save loses it.
        self._save(record)

    def _save(self, *records: dict[str, Any]) -> bool:
        """Hand records to the store, without the queue lock; whether all were saved."""
        if self._store is None:
            return True
        saved = True
        for record in records:
            saved = self._store.save(record) and saved
        return saved

    def _enqueue(self, job: PromptJob) -> None:
        """Make an accepted job visible to readers and the worker; caller holds the lock."""
        self._pending.append(job)
        self._jobs[job.id] = job
        self._available.notify()

    def _claim(self, parent: PromptJob, job: PromptJob, known_revision: int, now: float) -> bool:
        """Record the answer as the parent's in the store, unless another task got there first.

        Under the store's lock the parent's newest record decides: a later change
        that is no longer waiting refuses with ``not_waiting``, an answer this
        task has not seen refuses with ``already_answered``; otherwise the
        follow-up and the answered parent are written together.
        """
        if self._store is None:
            return True

        def decide(stored: dict[str, Any] | None) -> list[dict[str, Any]]:
            latest = PromptJob.from_record(stored) if stored is not None else None
            if latest is not None and latest.revision >= known_revision:
                if latest.revision > known_revision and latest.state is not PromptState.NEEDS_INPUT:
                    raise AnswerRefused(NOT_WAITING)
                if latest.answered_by:
                    raise AnswerRefused(ALREADY_ANSWERED)
            with job._lock:
                follow_up_record = job._bump(now)
            with parent._lock:
                if latest is not None:
                    # Stay above whatever the store holds, so this record wins on reload.
                    parent.revision = max(parent.revision, latest.revision)
                parent_record = parent._bump(now)
            return [follow_up_record, parent_record]

        return self._store.compare_and_append(parent.id, decide)

    def _release(self, parent: PromptJob, job: PromptJob) -> None:
        """Undo an answer that was not accepted: free its slot and the parent's hold."""
        with self._lock:
            self._reserved -= 1
            with parent._lock:
                if parent.answered_by == job.id:
                    parent.answered_by = ""

    def _reread_waiting(self, prompt_id: str) -> None:
        """Re-read a waiting question, at most once per ``refresh_seconds``.

        Another task sharing the store may have accepted an answer to it.
        """
        if self._store is None:
            return
        now = self._clock()
        with self._lock:
            job = self._jobs.get(prompt_id)
            if job is None or job.state is not PromptState.NEEDS_INPUT or job.answered_by:
                return
            last = self._reread_at.get(prompt_id)
            if last is not None and now - last < self._refresh_seconds:
                return
            self._reread_at[prompt_id] = now
        self._reread(job)

    def _reread(self, job: PromptJob) -> None:
        """Take the store's newer state of ``job``, and the other task's answer to it."""
        if self._store is None:
            return
        stored = {str(record.get("id", "")): record for record in self._store.load()}
        with self._lock:
            if self._jobs.get(job.id) is job:
                self._adopt_stored(stored, job)

    def _adopt_stored(self, stored: Mapping[str, Mapping[str, Any]], job: PromptJob) -> None:
        """Adopt a newer stored ``job``; track an answer to it this task does not hold.

        The caller holds the lock. Such an answer belongs to another task, so it is
        re-read like any foreign prompt and taken over if that task dies.
        """
        newer = PromptJob.from_record(stored.get(job.id, {}))
        if newer is not None:
            with job._lock:
                job._adopt(newer)
        follow_up_id = job.answered_by
        if not follow_up_id or follow_up_id in self._jobs:
            return
        follow_up = PromptJob.from_record(stored.get(follow_up_id, {}))
        if follow_up is not None:
            self._jobs[follow_up_id] = follow_up
            if not follow_up.settled:
                self._foreign.add(follow_up_id)

    def _refresh_foreign(self) -> None:
        """Re-read the prompts another task owns, and take over those whose owner went silent.

        Costs nothing while no such prompt is held, and reads the store at most
        once per ``refresh_seconds`` otherwise.
        """
        if self._store is None:
            return
        now = self._clock()
        with self._lock:
            if not self._foreign:
                return
            if (
                self._foreign_read_at is not None
                and now - self._foreign_read_at < self._refresh_seconds
            ):
                return
            self._foreign_read_at = now
        stored = {str(record.get("id", "")): record for record in self._store.load()}
        records: list[dict[str, Any]] = []
        with self._lock:
            for job_id in list(self._foreign):
                job = self._jobs.get(job_id)
                if job is None:
                    self._foreign.discard(job_id)
                    continue
                # The owner may also have changed the parent (a reopened question).
                for held in (job, self._jobs.get(job.parent_id)):
                    if held is not None:
                        self._adopt_stored(stored, held)
                if job.settled:
                    self._foreign.discard(job_id)
                elif not self._alive(job, now):
                    for taken in self._take_over(job, now):
                        with taken._lock:
                            records.append(taken._bump(now))
        self._save(*records)

    def _alive(self, job: PromptJob, now: float) -> bool:
        """Whether the task that owns ``job`` saved it recently enough to still be running."""
        return job.heartbeat_at is not None and job.heartbeat_at >= now - self._stale_seconds

    def _take_over(self, job: PromptJob, now: float) -> list[PromptJob]:
        """Settle a job whose owner died as ``interrupted`` and reopen the question it answered.

        Returns the jobs it changed, for the caller to save; the caller holds the lock.
        """
        self._foreign.discard(job.id)
        with job._lock:
            job.state = PromptState.FAILED
            job.error_code = ERROR_INTERRUPTED
            job.finished_at = now
        changed = [job]
        parent = self._jobs.get(job.parent_id) if job.parent_id else None
        if parent is not None and parent.answered_by == job.id:
            self._reopen(parent, now)
            changed.append(parent)
        return changed

    @staticmethod
    def _reopen(parent: PromptJob, now: float) -> None:
        """Let ``parent`` take an answer again, with a fresh retention window."""
        with parent._lock:
            parent.answered_by = ""
            parent.finished_at = now

    def _expired_ids(self, now: float) -> list[str]:
        """Settled jobs past retention whose question no unsettled answer is still using.

        A parent whose follow-up is queued or running stays, so that answer's
        outcome (or its interruption, which reopens the question) has a parent.
        """
        cutoff = now - self._retention_seconds
        expired: list[str] = []
        for job_id, job in self._jobs.items():
            if not job.settled or (job.finished_at is not None and job.finished_at >= cutoff):
                continue
            follow_up = self._jobs.get(job.answered_by) if job.answered_by else None
            if follow_up is not None and not follow_up.settled:
                continue
            expired.append(job_id)
        return expired

    def _forget_expired(self) -> None:
        """Drop settled results older than the retention window; caller holds the lock."""
        for job_id in self._expired_ids(self._clock()):
            self._reread_at.pop(job_id, None)
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
    "PromptNotSaved",
    "PromptQueue",
    "PromptState",
]

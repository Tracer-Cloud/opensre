"""Prompt jobs: what a remote caller submitted and what became of it."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from config.constants.gateway import PROMPT_QUEUE_MAX, PROMPT_RESULT_RETENTION_SECONDS


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
            if self.state is PromptState.FAILED:
                record["error"] = self.error_code
            if self.finished_at is not None:
                record["finished_at"] = self.finished_at
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

    def finish(self, job: PromptJob, answer: str) -> None:
        self._settle(job, PromptState.DONE, answer=answer)

    def needs_input(self, job: PromptJob, question: str) -> None:
        self._settle(job, PromptState.NEEDS_INPUT, question=question)

    def fail(self, job: PromptJob, error_code: str) -> None:
        self._settle(job, PromptState.FAILED, error_code=error_code)

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
        error_code: str = "",
    ) -> None:
        with job._lock:
            job.state = state
            job.answer = answer
            job.question = question
            job.error_code = error_code
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
            del self._jobs[job_id]


__all__ = ["PromptJob", "PromptQueue", "PromptState"]

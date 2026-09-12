"""Durable identity, deadline, and evidence for one bounded repair."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class RepairStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class RepairRun(BaseModel):
    """A run retains its deadline and scope across retries and process restarts."""

    id: str
    owner: str
    repo: str
    actor: str
    demo: bool
    started_at: float
    deadline: float
    pr_number: int = 0
    workspace: str = ""
    status: RepairStatus = RepairStatus.QUEUED
    finished_at: float | None = None
    attempts: int = 0
    base_branch: str = ""
    branch: str = ""
    repository_id: int = 0
    created_repository: bool = False
    initial_sha: str = ""
    fixed_sha: str = ""
    failed_run_url: str = ""
    passed_run_url: str = ""
    checks_passed: bool = False
    cleanup: str = "Temporary artifacts retained."
    reason: str = "Waiting for the scheduled tick."
    attempt_errors: list[str] = Field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.status not in {RepairStatus.QUEUED, RepairStatus.RUNNING}

    @property
    def identity(self) -> tuple[str, str, str, int]:
        return (
            self.actor.casefold(),
            self.owner.casefold(),
            self.repo.casefold(),
            (0 if self.demo else self.pr_number),
        )

    @property
    def repository_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def pr_url(self) -> str:
        return f"{self.repository_url}/pull/{self.pr_number}" if self.pr_number else ""

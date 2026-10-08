"""Durable identity, deadline, and evidence for one bounded repair."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class RepairRefused(ValueError):
    """A scheduling refusal; ``user_message`` is text written for the user, not error detail."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


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
    # Legacy records remain readable locally but cannot authorize an account.
    actor_id: int = Field(default=0, ge=0, strict=True)
    #: Set only on records of the retired fixed-repository demo. Such a record still
    #: loads, but the worker refuses to run it.
    demo: bool = False
    #: Set only for a pull request this process seeded as the demo: short check waits,
    #: the repair may change only calculator.py, and analytics count the run as the
    #: demo. A repository name does not set this; an ordinary repair of a similarly
    #: named repo waits and edits as usual.
    fast_checks: bool = False
    #: The seeded demo's head commit when it was scheduled. A head that is neither this
    #: commit nor one this run pushed clears ``fast_checks``: the run continues, and is
    #: counted, as an ordinary repair. Empty on records that predate it.
    seeded_head: str = ""
    #: Scheduled by a gateway's own scheduler (the hosted gateway), not the user's shell.
    remote: bool = False
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
    registered: bool = False
    initial_sha: str = ""
    fixed_sha: str = ""
    failed_run_url: str = ""
    passed_run_url: str = ""
    checks_passed: bool = False
    cleanup: str = "Temporary artifacts retained."
    reason: str = "Waiting for the scheduled tick."
    attempt_errors: list[str] = Field(default_factory=list)
    #: Heads this run pushed; only one of them may be credited as the repair commit.
    pushed_shas: list[str] = Field(default_factory=list)
    #: Coding-agent backend the worker selected ("codex", "claude-code", ...); empty until then.
    coding_agent: str = ""
    #: Monotonic wall seconds per worker phase, summed over the run. Each
    #: ``attempt-N.json`` holds the share timed since the previous attempt record.
    phase_seconds: dict[str, float] = Field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.status not in {RepairStatus.QUEUED, RepairStatus.RUNNING}

    @property
    def identity(self) -> tuple[int, str, str, int]:
        return (self.actor_id, self.owner.casefold(), self.repo.casefold(), self.pr_number)

    @property
    def repository_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def pr_url(self) -> str:
        return f"{self.repository_url}/pull/{self.pr_number}" if self.pr_number else ""

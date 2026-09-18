"""Work completion evidence, independent of report delivery."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from config.constants.scheduler import NON_RETRYABLE_WORK_ERROR_KINDS


class WorkStatus(StrEnum):
    UNKNOWN = "unknown"
    SUCCEEDED = "succeeded"
    NOOP = "noop"
    BLOCKED = "blocked"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


class WorkOutcome(BaseModel):
    """A producer's verified terminal outcome for one operation."""

    status: WorkStatus = WorkStatus.UNKNOWN
    error_kind: str = ""
    operation: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = True

    @model_validator(mode="before")
    @classmethod
    def _default_retryable_from_error_kind(cls, data: Any) -> Any:
        """Rows persisted before ``retryable`` existed derive it from ``error_kind``.

        Without this, a replayed ``pr_not_open`` block reads as retryable and the
        executor fires the schedule again at a target it already knows is stuck.
        """
        if isinstance(data, dict) and "retryable" not in data:
            kind = data.get("error_kind", "")
            return {**data, "retryable": kind not in NON_RETRYABLE_WORK_ERROR_KINDS}
        return data

    @property
    def completed(self) -> bool:
        return self.status in {WorkStatus.SUCCEEDED, WorkStatus.NOOP}

    @property
    def terminal_block(self) -> bool:
        """A block no retry can clear; the schedule should pause on it."""
        return self.status is WorkStatus.BLOCKED and not self.retryable

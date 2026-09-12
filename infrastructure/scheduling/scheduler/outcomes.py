"""Work completion evidence, independent of report delivery."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


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

    @property
    def completed(self) -> bool:
        return self.status in {WorkStatus.SUCCEEDED, WorkStatus.NOOP}

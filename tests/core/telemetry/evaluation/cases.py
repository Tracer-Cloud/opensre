"""Validated incident inputs with evaluator-only expectations."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.telemetry.models import Evidence, Measurement, Scope


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class IncidentInput(Contract):
    service: str = Field(min_length=1)
    environment: str = Field(min_length=1)
    start: float
    end: float
    total: float = Field(gt=0)
    errors: float = Field(ge=0)
    evidence: Evidence

    @model_validator(mode="after")
    def valid_window_and_counts(self) -> IncidentInput:
        if self.start >= self.end or self.errors > self.total:
            raise ValueError("Incident window or request counts are inconsistent")
        return self

    def scope(self) -> Scope:
        return Scope(self.service, self.environment, self.start, self.end)

    def measurement(self) -> Measurement:
        return Measurement(self.total, self.errors, self.end)


class Expectations(Contract):
    required_observations: list[str] = Field(min_length=1)
    justified_conclusions: list[str] = Field(min_length=1)
    unsupported_conclusions: list[str] = Field(min_length=1)
    acceptable_next_checks: list[str] = Field(min_length=1)


class IncidentCase(Contract):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    family: str = Field(min_length=1)
    category: Literal["clear", "insufficient", "misleading", "untrusted"]
    split: Literal["development", "holdout"]
    origin: Literal["synthetic"]
    review_status: Literal["pending_sre_review", "sre_reviewed"]
    incident: IncidentInput
    expectations: Expectations


class CaseSet(Contract):
    version: Literal[1]
    cases: list[IncidentCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases_and_grouped_splits(self) -> CaseSet:
        ids: set[str] = set()
        families: dict[str, str] = {}
        for case in self.cases:
            if case.id in ids:
                raise ValueError(f"Duplicate case: {case.id}")
            ids.add(case.id)
            if families.setdefault(case.family, case.split) != case.split:
                raise ValueError(f"Family crosses development/holdout splits: {case.family}")
        return self


def load_cases(path: Path) -> tuple[CaseSet, str]:
    raw = path.read_bytes()
    return CaseSet.model_validate_json(raw), hashlib.sha256(raw).hexdigest()

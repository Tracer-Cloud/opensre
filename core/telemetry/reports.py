"""Evidence snapshots, validated analysis and the retained investigation report."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from config.constants.telemetry import TELEMETRY_INVESTIGATION_MAX_ITEMS, TELEMETRY_REPORT_MAX_CHARS
from core.telemetry.investigation import sanitize
from core.telemetry.models import Evidence, Measurement, Scope
from infrastructure.safety.secret_redaction import redact_values

type Reference = Annotated[str, Field(min_length=1, max_length=32)]
type Text = Annotated[str, Field(min_length=1, max_length=180)]
FailureReason = Literal[
    "source_unavailable",
    "model_unavailable",
    "deadline",
    "invalid_report",
    "input_limit",
    "invalid_input",
]


class ReportValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Fact(ReportValue):
    text: Text
    evidence_refs: tuple[Reference, ...] = Field(min_length=1, max_length=2)

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        value = sanitize(value).strip()
        if not value:
            raise ValueError("Report text must remain nonempty after sanitization")
        if len(value) > 180:
            raise ValueError("Sanitized report text exceeds its field budget")
        return value


class Hypothesis(ReportValue):
    text: Text
    supporting_refs: tuple[Reference, ...] = Field(min_length=1, max_length=2)
    contradicting_refs: tuple[Reference, ...] = Field(max_length=2)

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return Fact.clean_text(value)

    @model_validator(mode="after")
    def disjoint_references(self) -> Hypothesis:
        if set(self.supporting_refs) & set(self.contradicting_refs):
            raise ValueError("Evidence cannot both support and contradict the same hypothesis")
        return self


class Analysis(ReportValue):
    """Model-authored content only; source identity, counters and failures are host-owned."""

    status: Literal["completed", "inconclusive"]
    facts: tuple[Fact, ...] = Field(max_length=4)
    hypotheses: tuple[Hypothesis, ...] = Field(max_length=2)
    unknowns: tuple[Text, ...] = Field(max_length=3)
    next_check: Text

    @field_validator("next_check")
    @classmethod
    def clean_next_check(cls, value: str) -> str:
        return Fact.clean_text(value)

    @field_validator("unknowns")
    @classmethod
    def clean_unknowns(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(Fact.clean_text(value) for value in values)

    @model_validator(mode="after")
    def justified_status(self) -> Analysis:
        if self.status == "inconclusive" and not self.unknowns:
            raise ValueError("Inconclusive analysis must identify missing evidence")
        if self.status == "completed" and not self.facts:
            raise ValueError("Completed analysis must contain cited facts")
        return self


@dataclass(frozen=True)
class EvidenceRecord:
    ref: str
    kind: Literal["counter", "trace", "log"]
    data_json: str


def evidence_snapshot(
    scope: Scope, sample: Measurement, evidence: Evidence
) -> tuple[tuple[EvidenceRecord, ...], str]:
    """Assign references to the exact redacted input, never evaluator labels or model text."""
    if (
        not all(
            math.isfinite(value) for value in (scope.start, scope.end, sample.total, sample.errors)
        )
        or not 0 <= sample.errors <= sample.total
        or (sample.latest is not None and not math.isfinite(sample.latest))
    ):
        raise ValueError("Invalid detector measurement or scope")
    safe_scope = redact_values(asdict(scope), redactor=sanitize)
    records: list[EvidenceRecord] = []

    def add(kind: Literal["counter", "trace", "log"], value: object) -> None:
        data = redact_values(value, redactor=sanitize)
        encoded = json.dumps(data, sort_keys=True, allow_nan=False)
        identity = json.dumps([safe_scope, kind, len(records), data], sort_keys=True)
        ref = kind + ":" + hashlib.sha256(identity.encode()).hexdigest()[:16]
        records.append(EvidenceRecord(ref, kind, encoded))

    add("counter", asdict(sample))
    sources: tuple[tuple[str, Literal["trace", "log"]], ...] = (
        ("traces", "trace"),
        ("logs", "log"),
    )
    for key, kind in sources:
        rows = evidence.get(key, [])
        if not isinstance(rows, list) or len(rows) > TELEMETRY_INVESTIGATION_MAX_ITEMS:
            raise ValueError("Evidence must contain at most ten rows per source")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Evidence row must be an object")
            add(kind, row)
    payload = json.dumps(
        {
            "scope": safe_scope,
            "evidence": [
                {"ref": record.ref, "kind": record.kind, "data": json.loads(record.data_json)}
                for record in records
            ],
            "limitations": (
                "Counter is the host detector measurement; stored traces/logs are bounded samples, "
                "not complete traces or population rates. Missing rows/attributes and late arrivals "
                "remain possible. Valid references do not prove a causal claim."
            ),
        },
        allow_nan=False,
    )
    return tuple(records), payload


@dataclass(frozen=True)
class InvestigationReport:
    scope: Scope
    measurement: Measurement | None
    evidence: tuple[EvidenceRecord, ...]
    analysis: Analysis
    failure_reason: FailureReason | None = None

    def __post_init__(self) -> None:
        known = {record.ref for record in self.evidence}
        for fact in self.analysis.facts:
            if not set(fact.evidence_refs) <= known:
                raise ValueError("Fact cites evidence outside the supplied snapshot")
        for hypothesis in self.analysis.hypotheses:
            if not set(hypothesis.supporting_refs + hypothesis.contradicting_refs) <= known:
                raise ValueError("Hypothesis cites evidence outside the supplied snapshot")
        if len(self.render()) > TELEMETRY_REPORT_MAX_CHARS:
            raise ValueError("Rendered report exceeds its delivery budget")

    @property
    def status(self) -> Literal["completed", "inconclusive", "partial"]:
        return "partial" if self.failure_reason else self.analysis.status

    def render(self) -> str:
        """Render validated content while preserving the authoritative measurement prefix."""
        lines = []
        if self.measurement is not None:
            lines.append(
                f"Observed: {self.measurement.errors:g}/{self.measurement.total:g} failed requests "
                "in the configured service/window."
            )
        lines.append(f"Investigation: {self.status}")
        if self.failure_reason:
            lines.append(f"Partial investigation: {self.failure_reason}. Cause unknown.")
        lines.append("Facts:")
        lines.extend(
            f"- {fact.text} [{', '.join(fact.evidence_refs)}]" for fact in self.analysis.facts
        )
        lines.append("Hypotheses (unverified):")
        for hypothesis in self.analysis.hypotheses:
            lines.append(f"- {hypothesis.text} [supports: {', '.join(hypothesis.supporting_refs)}]")
            if hypothesis.contradicting_refs:
                lines.append(f"  Contradicts: {', '.join(hypothesis.contradicting_refs)}")
        if not self.analysis.hypotheses:
            lines.append("- Cause unknown.")
        lines.append("Unknowns:")
        lines.extend(f"- {unknown}" for unknown in self.analysis.unknowns)
        lines.append(f"Next check: {self.analysis.next_check}")
        lines.append(
            "Limits: sampled evidence; citations establish identity, not causality. Recovery is measured separately."
        )
        return "\n".join(lines)

    def to_json(self) -> str:
        """Retain the validated structure and the evidence needed to resolve every reference."""
        return json.dumps(
            {
                "schema_version": 1,
                "status": self.status,
                "failure_reason": self.failure_reason,
                "scope": redact_values(asdict(self.scope), redactor=sanitize),
                "measurement": asdict(self.measurement) if self.measurement else None,
                "evidence": [
                    {"ref": record.ref, "kind": record.kind, "data": json.loads(record.data_json)}
                    for record in self.evidence
                ],
                "analysis": self.analysis.model_dump(mode="json"),
            },
            allow_nan=False,
        )


def partial_report(
    scope: Scope,
    sample: Measurement | None,
    reason: FailureReason,
    evidence: tuple[EvidenceRecord, ...] = (),
) -> InvestigationReport:
    """Keep detector facts even when analysis fails; never manufacture a diagnosis."""
    if sample is not None and (
        not math.isfinite(sample.total)
        or not math.isfinite(sample.errors)
        or not 0 <= sample.errors <= sample.total
        or (sample.latest is not None and not math.isfinite(sample.latest))
    ):
        sample = None
    return InvestigationReport(
        scope,
        sample,
        evidence,
        Analysis(
            status="inconclusive",
            facts=(),
            hypotheses=(),
            unknowns=("Evidence or analysis was unavailable; cause remains unverified.",),
            next_check="Inspect the retained incident window and telemetry source; retry analysis manually.",
        ),
        reason,
    )

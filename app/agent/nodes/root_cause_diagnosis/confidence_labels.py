"""Confidence and validity label rules.

This module documents explicit label rules for RCA assessment. It is a draft
specification that can be wired into the diagnosis flow.
"""

from dataclasses import dataclass
from typing import Iterable

VALIDITY_LEVELS = ("strong", "mixed", "weak")
CONFIDENCE_LEVELS = ("high", "medium", "low")

CRITICAL_CLAIM_KEYWORDS = (
    "root cause",
    "failure",
    "failed",
    "caused by",
    "causing",
    "due to",
    "timeout",
    "schema change",
    "breaking change",
)


@dataclass(frozen=True)
class EvidenceCoverage:
    total_claims: int
    validated_claims: int
    critical_claims_total: int
    critical_claims_validated: int
    contradictions: int = 0

    def validated_ratio(self) -> float:
        return self.validated_claims / self.total_claims if self.total_claims else 0.0

    def critical_ratio(self) -> float:
        if self.critical_claims_total == 0:
            return 1.0
        return self.critical_claims_validated / self.critical_claims_total


@dataclass(frozen=True)
class EvidenceCompleteness:
    missing_critical: tuple[str, ...] = ()
    missing_important: tuple[str, ...] = ()
    missing_nice: tuple[str, ...] = ()


@dataclass(frozen=True)
class AlertEvidenceProfile:
    critical: tuple[str, ...]
    important: tuple[str, ...] = ()
    nice_to_have: tuple[str, ...] = ()


DEFAULT_EVIDENCE_PROFILE = AlertEvidenceProfile(
    critical=("cloudwatch_logs", "error_logs"),
    important=("failed_jobs", "host_metrics"),
    nice_to_have=("lambda_logs", "s3_object"),
)

ALERT_EVIDENCE_PROFILES = {
    "lambda": AlertEvidenceProfile(
        critical=("cloudwatch_logs", "lambda_logs"),
        important=("lambda_config", "host_metrics"),
        nice_to_have=("s3_audit_payload", "vendor_audit_from_logs"),
    ),
    "batch": AlertEvidenceProfile(
        critical=("failed_jobs", "error_logs"),
        important=("cloudwatch_logs", "host_metrics"),
        nice_to_have=("lambda_logs", "s3_object"),
    ),
    "s3": AlertEvidenceProfile(
        critical=("s3_object", "error_logs"),
        important=("cloudwatch_logs", "s3_audit_payload"),
        nice_to_have=("vendor_audit_from_logs", "lambda_logs"),
    ),
    "vendor": AlertEvidenceProfile(
        critical=("vendor_audit_from_logs", "s3_audit_payload"),
        important=("cloudwatch_logs", "error_logs"),
        nice_to_have=("lambda_config", "host_metrics"),
    ),
}

ROUTING_RULES = (
    "If confidence is low or validity is weak, continue investigation (if actionable).",
    "If confidence is medium and validity is mixed, continue until loop cap.",
    "If confidence is high and validity is strong, publish findings.",
    "If loop cap reached, publish with caveats.",
)

MEMORY_PERSISTENCE_RULES = (
    "high/strong: persist full memory",
    "medium/mixed: persist tentative memory",
    "low/weak: skip or minimal summary only",
)


def select_evidence_profile(alert_name: str | None) -> AlertEvidenceProfile:
    if not alert_name:
        return DEFAULT_EVIDENCE_PROFILE
    lowered = alert_name.lower()
    for keyword, profile in ALERT_EVIDENCE_PROFILES.items():
        if keyword in lowered:
            return profile
    return DEFAULT_EVIDENCE_PROFILE


def calculate_validity_level(coverage: EvidenceCoverage) -> tuple[str, str]:
    if coverage.total_claims == 0:
        return "weak", "No claims were produced; evidence validation is not possible."

    if coverage.contradictions > 0:
        return "weak", "Contradictions found in evidence against critical claims."

    validated_ratio = coverage.validated_ratio()
    critical_ratio = coverage.critical_ratio()

    if validated_ratio >= 0.8 and critical_ratio == 1.0:
        rationale = (
            f"Most claims validated ({coverage.validated_claims}/{coverage.total_claims}) "
            "and all critical claims are supported."
        )
        return "strong", rationale

    if validated_ratio >= 0.5:
        rationale = (
            f"Some claims validated ({coverage.validated_claims}/{coverage.total_claims}); "
            "critical gaps or missing evidence remain."
        )
        return "mixed", rationale

    return (
        "weak",
        f"Most claims unvalidated ({coverage.validated_claims}/{coverage.total_claims}) "
        "or critical evidence is missing.",
    )


def calculate_confidence_level(
    validity_level: str,
    completeness: EvidenceCompleteness,
    has_direct_evidence: bool,
) -> tuple[str, str]:
    if completeness.missing_critical:
        return "low", _format_missing("Missing critical evidence", completeness.missing_critical)

    if validity_level == "weak":
        return "low", "Validity is weak and evidence gaps remain."

    if completeness.missing_important:
        return "medium", _format_missing("Missing important evidence", completeness.missing_important)

    if validity_level == "mixed":
        return "medium", "Validity is mixed; some claims lack direct support."

    if not has_direct_evidence:
        return "medium", "Evidence is correlational; direct error logs or traces are missing."

    return "high", "Direct evidence is present and validity is strong."


def _format_missing(prefix: str, missing: Iterable[str]) -> str:
    missing_list = ", ".join(sorted(set(missing)))
    return f"{prefix}: {missing_list}."

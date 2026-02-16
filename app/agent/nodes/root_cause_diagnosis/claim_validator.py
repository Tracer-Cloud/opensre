"""Claim validation against collected evidence."""

from typing import Any


def validate_claim(claim: str, evidence: dict[str, Any]) -> bool:
    """
    Validate if a claim is supported by available evidence.

    Args:
        claim: The claim to validate
        evidence: Collected evidence dictionary

    Returns:
        True if claim is supported by evidence
    """
    claim_lower = claim.lower()

    # Check logs (from evidence - including Grafana logs)
    has_any_logs = (
        evidence.get("total_logs", 0) > 0
        or evidence.get("grafana_logs")
        or evidence.get("grafana_error_logs")
    )
    if ("log" in claim_lower or "error" in claim_lower) and not has_any_logs:
        return False

    # Check metrics (from evidence)
    if ("memory" in claim_lower or "cpu" in claim_lower) and not evidence.get(
        "host_metrics", {}
    ).get("data"):
        return False

    # Check jobs (from evidence)
    if ("job" in claim_lower or "batch" in claim_lower) and len(
        evidence.get("failed_jobs", [])
    ) == 0:
        return False

    # Check lambda evidence
    if ("lambda" in claim_lower or "function" in claim_lower) and not (
        evidence.get("lambda_logs") or evidence.get("lambda_function")
    ):
        return False

    # Check s3 evidence
    if ("s3" in claim_lower or "bucket" in claim_lower or "object" in claim_lower) and not (
        evidence.get("s3_object") or evidence.get("s3_objects")
    ):
        return False

    # Check schema/metadata evidence
    if ("schema" in claim_lower or "metadata" in claim_lower) and not evidence.get(
        "s3_object", {}
    ).get("metadata"):
        return False

    # Check vendor/external API evidence
    return not (
        ("vendor" in claim_lower or "external api" in claim_lower or "api" in claim_lower)
        and not (
            evidence.get("vendor_audit_from_logs")
            or evidence.get("s3_audit_payload")
            or evidence.get("lambda_config", {}).get("environment_variables")
        )
    )


def extract_evidence_sources(claim: str, evidence: dict[str, Any]) -> list[str]:
    """
    Extract which evidence sources support a claim.

    Args:
        claim: The claim text
        evidence: Collected evidence dictionary

    Returns:
        List of evidence source names that support the claim
    """
    sources = []
    claim_lower = claim.lower()

    if ("log" in claim_lower or "error" in claim_lower) and evidence.get("cloudwatch_logs"):
        sources.append("cloudwatch_logs")
    if ("log" in claim_lower or "error" in claim_lower) and evidence.get("total_logs", 0) > 0:
        sources.append("logs")
    if ("job" in claim_lower or "batch" in claim_lower) and evidence.get("failed_jobs"):
        sources.append("aws_batch_jobs")
    if "tool" in claim_lower and evidence.get("failed_tools"):
        sources.append("tracer_tools")
    if (
        "metric" in claim_lower or "memory" in claim_lower or "cpu" in claim_lower
    ) and evidence.get("host_metrics", {}).get("data"):
        sources.append("host_metrics")
    if ("lambda" in claim_lower or "function" in claim_lower) and (
        evidence.get("lambda_logs") or evidence.get("lambda_function")
    ):
        sources.append("lambda_logs")
    if "code" in claim_lower and evidence.get("lambda_function", {}).get("code"):
        sources.append("lambda_code")
    if ("s3" in claim_lower or "bucket" in claim_lower or "object" in claim_lower) and evidence.get(
        "s3_object"
    ):
        sources.append("s3_metadata")
    if ("schema" in claim_lower or "metadata" in claim_lower) and evidence.get("s3_object", {}).get(
        "metadata"
    ):
        sources.append("s3_metadata")
    if ("vendor" in claim_lower or "external" in claim_lower or "api" in claim_lower) and (
        evidence.get("vendor_audit_from_logs") or evidence.get("s3_audit_payload")
    ):
        sources.append("vendor_audit")
    if ("environment" in claim_lower or "env" in claim_lower) and evidence.get(
        "lambda_config", {}
    ).get("environment_variables"):
        sources.append("lambda_config")
    if "audit" in claim_lower and evidence.get("s3_audit_payload"):
        sources.append("s3_audit")
    if ("log" in claim_lower or "error" in claim_lower or "grafana" in claim_lower) and (
        evidence.get("grafana_logs") or evidence.get("grafana_error_logs")
    ):
        sources.append("grafana_logs")
    if ("trace" in claim_lower or "span" in claim_lower or "pipeline" in claim_lower) and evidence.get(
        "grafana_pipeline_spans"
    ):
        sources.append("grafana_traces")
    if ("metric" in claim_lower or "rate" in claim_lower or "count" in claim_lower) and evidence.get(
        "grafana_metrics"
    ):
        sources.append("grafana_metrics")

    return sources if sources else ["evidence_analysis"]


def validate_and_categorize_claims(
    validated_claims: list[str],
    non_validated_claims: list[str],
    evidence: dict[str, Any],
) -> tuple[list[dict], list[dict]]:
    """
    Validate claims and categorize them with evidence sources.

    Args:
        validated_claims: Claims marked as validated by LLM
        non_validated_claims: Claims marked as non-validated by LLM
        evidence: Collected evidence dictionary

    Returns:
        Tuple of (validated_claims_list, non_validated_claims_list)
    """
    validated_claims_list = []
    non_validated_claims_list = []

    for claim in validated_claims:
        is_valid = validate_claim(claim, evidence)
        validated_claims_list.append(
            {
                "claim": claim,
                "evidence_sources": extract_evidence_sources(claim, evidence),
                "validation_status": "validated" if is_valid else "failed_validation",
            }
        )

    for claim in non_validated_claims:
        is_valid = validate_claim(claim, evidence)
        if is_valid:
            validated_claims_list.append(
                {
                    "claim": claim,
                    "evidence_sources": extract_evidence_sources(claim, evidence),
                    "validation_status": "validated",
                }
            )
        else:
            non_validated_claims_list.append(
                {
                    "claim": claim,
                    "validation_status": "not_validated",
                }
            )

    return validated_claims_list, non_validated_claims_list


def calculate_validity_score(validated_claims: list[dict], non_validated_claims: list[dict]) -> float:
    """
    Calculate validity score based on claim validation.

    Args:
        validated_claims: List of validated claim dictionaries
        non_validated_claims: List of non-validated claim dictionaries

    Returns:
        Validity score (0.0 to 1.0)
    """
    total_claims = len(validated_claims) + len(non_validated_claims)
    return len(validated_claims) / total_claims if total_claims > 0 else 0.0

"""Claim validation against collected evidence."""

from typing import Any


def _has_datadog_evidence(evidence: dict[str, Any]) -> bool:
    return bool(
        evidence.get("datadog_logs")
        or evidence.get("datadog_error_logs")
        or evidence.get("datadog_monitors")
        or evidence.get("datadog_events")
    )


def _has_any_logs(evidence: dict[str, Any]) -> bool:
    return bool(
        evidence.get("total_logs", 0) > 0
        or evidence.get("grafana_logs")
        or evidence.get("grafana_error_logs")
        or evidence.get("datadog_logs")
        or evidence.get("datadog_error_logs")
        or evidence.get("cloudwatch_logs")
    )


def _has_rds_metrics(evidence: dict[str, Any]) -> bool:
    metrics = evidence.get("aws_cloudwatch_metrics", {})
    return bool(metrics and (metrics.get("metrics") or metrics.get("observations")))


def _has_rds_events(evidence: dict[str, Any]) -> bool:
    return bool(evidence.get("aws_rds_events"))


def _has_performance_insights(evidence: dict[str, Any]) -> bool:
    insights = evidence.get("aws_performance_insights", {})
    return bool(insights and (insights.get("top_sql") or insights.get("wait_events") or insights.get("observations")))


def _datadog_logs_contain(evidence: dict[str, Any], keywords: tuple[str, ...]) -> bool:
    """Check if any Datadog log message contains at least one of the given keywords."""
    for log in evidence.get("datadog_error_logs", []) + evidence.get("datadog_logs", []):
        msg = log.get("message", "").lower() if isinstance(log, dict) else ""
        if any(kw in msg for kw in keywords):
            return True
    return False


def validate_claim(claim: str, evidence: dict[str, Any]) -> bool:
    """Validate if a claim is supported by available evidence."""
    claim_lower = claim.lower()

    has_dd = _has_datadog_evidence(evidence)

    if ("log" in claim_lower or "error" in claim_lower or "fail" in claim_lower) and not _has_any_logs(evidence):
        return False

    if ("memory" in claim_lower or "cpu" in claim_lower) and not (
        evidence.get("host_metrics", {}).get("data")
        or _has_rds_metrics(evidence)
        or _has_performance_insights(evidence)
        or any(kw in claim_lower for kw in ("monitor", "datadog")) and has_dd
    ):
        return False

    if any(
        kw in claim_lower
        for kw in (
            "rds",
            "postgres",
            "database",
            "replica",
            "replication lag",
            "connection",
            "storage",
            "disk",
            "failover",
            "reboot",
        )
    ) and not (_has_rds_metrics(evidence) or _has_rds_events(evidence) or _has_performance_insights(evidence)):
        return False

    if ("query" in claim_lower or "sql" in claim_lower or "wait event" in claim_lower) and not _has_performance_insights(evidence):
        return False

    if ("job" in claim_lower or "batch" in claim_lower) and not (
        evidence.get("failed_jobs") or has_dd
    ):
        return False

    if ("lambda" in claim_lower or "function" in claim_lower) and not (
        evidence.get("lambda_logs") or evidence.get("lambda_function")
    ):
        return False

    if ("s3" in claim_lower or "bucket" in claim_lower or "object" in claim_lower) and not (
        evidence.get("s3_object") or evidence.get("s3_objects")
    ):
        return False

    if "schema" in claim_lower and not (
        evidence.get("s3_object", {}).get("metadata")
        or _datadog_logs_contain(evidence, ("schema", "field", "missing", "validation"))
    ):
        return False

    if (
        ("vendor" in claim_lower or "external api" in claim_lower)
        and not (
            evidence.get("vendor_audit_from_logs")
            or evidence.get("s3_audit_payload")
            or evidence.get("lambda_config", {}).get("environment_variables")
        )
    ):
        return False

    if any(kw in claim_lower for kw in ("kubernetes", "k8s", "container", "manifest", "pod")) and not has_dd:
        return False

    return not (
        any(kw in claim_lower for kw in ("pipeline", "failed", "failure"))
        and not (has_dd or _has_any_logs(evidence) or evidence.get("failed_jobs"))
    )


def extract_evidence_sources(claim: str, evidence: dict[str, Any]) -> list[str]:
    """Extract which evidence sources support a claim."""
    sources = []
    claim_lower = claim.lower()

    has_dd_logs = bool(evidence.get("datadog_logs") or evidence.get("datadog_error_logs"))

    if ("log" in claim_lower or "error" in claim_lower or "fail" in claim_lower) and evidence.get("cloudwatch_logs"):
        sources.append("cloudwatch_logs")
    if ("log" in claim_lower or "error" in claim_lower or "fail" in claim_lower) and evidence.get("total_logs", 0) > 0:
        sources.append("logs")
    if ("job" in claim_lower or "batch" in claim_lower) and evidence.get("failed_jobs"):
        sources.append("aws_batch_jobs")
    if "tool" in claim_lower and evidence.get("failed_tools"):
        sources.append("tracer_tools")
    if ("metric" in claim_lower or "memory" in claim_lower or "cpu" in claim_lower) and evidence.get("host_metrics", {}).get("data"):
        sources.append("host_metrics")
    if any(
        kw in claim_lower
        for kw in ("metric", "replica", "replication lag", "connection", "storage", "disk", "database", "rds")
    ) and _has_rds_metrics(evidence):
        sources.append("aws_cloudwatch_metrics")
    if any(kw in claim_lower for kw in ("event", "failover", "reboot", "promotion", "availability zone")) and _has_rds_events(evidence):
        sources.append("aws_rds_events")
    if any(kw in claim_lower for kw in ("query", "sql", "db load", "wait event", "cpu", "load")) and _has_performance_insights(evidence):
        sources.append("aws_performance_insights")
    if ("lambda" in claim_lower or "function" in claim_lower) and (
        evidence.get("lambda_logs") or evidence.get("lambda_function")
    ):
        sources.append("lambda_logs")
    if "code" in claim_lower and evidence.get("lambda_function", {}).get("code"):
        sources.append("lambda_code")
    if ("s3" in claim_lower or "bucket" in claim_lower or "object" in claim_lower) and evidence.get("s3_object"):
        sources.append("s3_metadata")
    if ("schema" in claim_lower or "metadata" in claim_lower) and evidence.get("s3_object", {}).get("metadata"):
        sources.append("s3_metadata")
    if ("vendor" in claim_lower or "external" in claim_lower) and (
        evidence.get("vendor_audit_from_logs") or evidence.get("s3_audit_payload")
    ):
        sources.append("vendor_audit")
    if ("environment" in claim_lower or "env" in claim_lower) and evidence.get("lambda_config", {}).get("environment_variables"):
        sources.append("lambda_config")
    if "audit" in claim_lower and evidence.get("s3_audit_payload"):
        sources.append("s3_audit")
    if ("log" in claim_lower or "error" in claim_lower or "grafana" in claim_lower) and (
        evidence.get("grafana_logs") or evidence.get("grafana_error_logs")
    ):
        sources.append("grafana_logs")
    if ("trace" in claim_lower or "span" in claim_lower or "pipeline" in claim_lower) and evidence.get("grafana_pipeline_spans"):
        sources.append("grafana_traces")
    if ("metric" in claim_lower or "rate" in claim_lower or "count" in claim_lower) and evidence.get("grafana_metrics"):
        sources.append("grafana_metrics")

    if has_dd_logs:
        if any(kw in claim_lower for kw in ("kubernetes", "k8s", "container", "job", "pod", "manifest", "namespace")):
            sources.append("datadog_logs")
        if any(kw in claim_lower for kw in ("configuration", "config", "environment variable", "env var", "schema", "field", "missing", "validation")):
            sources.append("datadog_logs")
        if any(kw in claim_lower for kw in ("error", "fail", "pipeline", "crash", "killed", "timeout")):
            sources.append("datadog_logs")

    if any(kw in claim_lower for kw in ("monitor", "alert", "triggered")) and evidence.get("datadog_monitors"):
        sources.append("datadog_monitors")

    if any(kw in claim_lower for kw in ("deploy", "event", "change")) and evidence.get("datadog_events"):
        sources.append("datadog_events")

    return list(dict.fromkeys(sources)) if sources else ["evidence_analysis"]


def validate_and_categorize_claims(
    validated_claims: list[str],
    non_validated_claims: list[str],
    evidence: dict[str, Any],
) -> tuple[list[dict], list[dict]]:
    """Validate claims and categorize them with evidence sources."""
    validated_claims_list = []
    non_validated_claims_list = []

    for claim in validated_claims:
        is_valid = validate_claim(claim, evidence)
        validated_claims_list.append({
            "claim": claim,
            "evidence_sources": extract_evidence_sources(claim, evidence),
            "validation_status": "validated" if is_valid else "failed_validation",
        })

    for claim in non_validated_claims:
        is_valid = validate_claim(claim, evidence)
        if is_valid:
            validated_claims_list.append({
                "claim": claim,
                "evidence_sources": extract_evidence_sources(claim, evidence),
                "validation_status": "validated",
            })
        else:
            non_validated_claims_list.append({
                "claim": claim,
                "validation_status": "not_validated",
            })

    return validated_claims_list, non_validated_claims_list


def calculate_validity_score(validated_claims: list[dict], non_validated_claims: list[dict]) -> float:
    """Calculate validity score weighted by evidence quality.

    Weights:
    - Validated claims with evidence sources: 1.0 each
    - Validated claims marked failed_validation: 0.5 each (LLM said validated, our check failed)
    - Non-validated claims: 0.0 each
    Bonus: +0.1 if any claim has non-generic evidence sources (not just evidence_analysis)
    """
    if not validated_claims and not non_validated_claims:
        return 0.0

    score = 0.0
    total_weight = 0.0

    for claim in validated_claims:
        sources = claim.get("evidence_sources", [])
        has_real_sources = any(s != "evidence_analysis" for s in sources)
        if claim.get("validation_status") == "validated":
            score += 1.0 if has_real_sources else 0.8
        else:
            score += 0.5
        total_weight += 1.0

    for _ in non_validated_claims:
        total_weight += 1.0

    base = score / total_weight if total_weight > 0 else 0.0

    has_real_evidence = any(
        s != "evidence_analysis"
        for claim in validated_claims
        for s in claim.get("evidence_sources", [])
    )
    bonus = 0.1 if has_real_evidence and validated_claims else 0.0

    return min(1.0, round(base + bonus, 2))

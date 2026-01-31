"""Investigation recommendation generation."""

from typing import Any


def generate_recommendations(
    evidence: dict[str, Any],
    confidence: float,
    validity_score: float,
    vendor_evidence_missing: bool,
) -> list[str]:
    """
    Generate investigation recommendations based on gaps in evidence.

    Args:
        evidence: Collected evidence
        confidence: Current confidence score
        validity_score: Current validity score
        vendor_evidence_missing: Whether vendor/external API evidence is missing

    Returns:
        List of recommendation strings (max 5)
    """
    should_recommend = confidence < 0.6 or validity_score < 0.5 or vendor_evidence_missing

    if not should_recommend:
        return []

    recommendations = []

    # Check what's missing from evidence
    if not evidence.get("host_metrics", {}).get("data"):
        recommendations.append("Query CloudWatch Metrics for CPU and memory usage")

    if evidence.get("total_logs", 0) == 0:
        recommendations.append("Fetch CloudWatch Logs for detailed error messages")

    if not evidence.get("failed_jobs"):
        recommendations.append("Query AWS Batch job details using describe_jobs API")

    # Check for upstream/vendor evidence gaps
    if not evidence.get("s3_object", {}).get("metadata"):
        recommendations.append("Inspect S3 object to get metadata and trace data lineage")

    if not evidence.get("vendor_audit_from_logs") and not evidence.get("s3_audit_payload"):
        recommendations.append("Retrieve Lambda configuration and logs from upstream functions")

    if evidence.get("s3_object", {}).get("metadata", {}).get("audit_key") and not evidence.get(
        "s3_audit_payload"
    ):
        recommendations.append("Fetch S3 audit payload to trace external vendor interactions")

    if not evidence.get("lambda_config") and not evidence.get("lambda_function"):
        recommendations.append(
            "Get Lambda function configuration to identify external dependencies"
        )

    return recommendations[:5]

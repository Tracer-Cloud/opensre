"""Evidence formatting and citation for RCA reports."""

from typing import Any

from app.agent.nodes.publish_findings.context.models import ReportContext
from app.agent.nodes.publish_findings.formatters.base import (
    format_json_block,
    format_json_payload,
    format_slack_link,
    format_text_block,
    shorten_text,
)
from app.agent.nodes.publish_findings.urls.aws import build_cloudwatch_url, build_lambda_console_url

# Evidence source labels for display
EVIDENCE_SOURCE_LABELS = {
    "cloudwatch_logs": "CloudWatch Logs",
    "lambda_function": "Lambda Function",
    "lambda_logs": "Lambda Invocation Logs",
    "lambda_errors": "Lambda Errors",
    "s3_object": "S3 Object Inspection",
    "s3_audit_payload": "S3 Audit Payload",
    "s3_metadata": "S3 Object Metadata",
    "s3_audit": "S3 Audit Trail",
    "vendor_audit": "External Vendor API Audit",
    "logs": "Error Logs",
    "aws_batch_jobs": "AWS Batch Jobs",
    "tracer_tools": "Tracer Tools",
    "host_metrics": "Host Metrics",
    "evidence_analysis": "Evidence Summary",
}


def sample_evidence_payload(source: str, evidence: dict) -> Any | None:
    """Extract a sample of evidence for a given source.

    Args:
        source: Evidence source identifier
        evidence: Full evidence dictionary from state

    Returns:
        Sample evidence data or None if not available

    Note:
        For lists, returns first 3 items. For objects, returns structured subset.
    """
    if source == "logs":
        logs = evidence.get("error_logs", [])
        return logs[:3] if logs else None

    if source == "aws_batch_jobs":
        failed_jobs = evidence.get("failed_jobs", [])
        return failed_jobs[:3] if failed_jobs else None

    if source == "tracer_tools":
        failed_tools = evidence.get("failed_tools", [])
        return failed_tools[:3] if failed_tools else None

    if source == "host_metrics":
        metrics = evidence.get("host_metrics", {}).get("data")
        return metrics if metrics else None

    if source == "cloudwatch_logs":
        cw_logs = evidence.get("cloudwatch_logs", [])
        return cw_logs[:3] if cw_logs else None

    if source == "lambda_function":
        lambda_func = evidence.get("lambda_function")
        return lambda_func if lambda_func else None

    if source == "lambda_logs":
        lambda_logs = evidence.get("lambda_logs", [])
        return lambda_logs[:3] if lambda_logs else None

    if source == "lambda_errors":
        lambda_errors = evidence.get("lambda_errors", [])
        return lambda_errors[:3] if lambda_errors else None

    if source == "s3_object":
        s3_obj = evidence.get("s3_object")
        if s3_obj:
            return {
                "bucket": s3_obj.get("bucket"),
                "key": s3_obj.get("key"),
                "metadata": s3_obj.get("metadata", {}),
                "size": s3_obj.get("size"),
                "is_text": s3_obj.get("is_text"),
            }
        return None

    if source == "s3_audit_payload":
        s3_audit = evidence.get("s3_audit_payload")
        if s3_audit:
            return {
                "bucket": s3_audit.get("bucket"),
                "key": s3_audit.get("key"),
                "content_preview": str(s3_audit.get("content", ""))[:500],
            }
        return None

    # Map legacy source names
    if source == "s3_metadata":
        return evidence.get("s3_object")

    if source == "s3_audit":
        return evidence.get("s3_audit_payload")

    if source == "vendor_audit":
        return evidence.get("vendor_audit_from_logs") or evidence.get("s3_audit_payload")

    if source == "evidence_analysis":
        return {
            "failed_jobs": len(evidence.get("failed_jobs", [])),
            "failed_tools": len(evidence.get("failed_tools", [])),
            "error_logs": len(evidence.get("error_logs", [])),
            "cloudwatch_logs": len(evidence.get("cloudwatch_logs", [])),
            "host_metrics": bool(evidence.get("host_metrics", {}).get("data")),
        }

    return None


def format_evidence_for_claim(claim_data: dict, evidence: dict, ctx: ReportContext) -> str:
    """Format evidence URLs or JSON for a specific claim.

    Args:
        claim_data: Claim dictionary with evidence_sources list
        evidence: Full evidence dictionary
        ctx: Report context for URL building

    Returns:
        Formatted string with evidence links or JSON snippets
    """
    evidence_sources = claim_data.get("evidence_sources", [])
    if not evidence_sources:
        return ""

    evidence_parts = []

    for source in evidence_sources:
        if source == "cloudwatch_logs":
            cw_url = build_cloudwatch_url(ctx)
            if cw_url:
                evidence_parts.append(f"*{format_slack_link('View CloudWatch Logs', cw_url)}*")

            # Also include sample log entries if available
            cloudwatch_logs = evidence.get("cloudwatch_logs", [])
            if cloudwatch_logs:
                sample_logs = cloudwatch_logs[:3]
                logs_preview = "\n".join(
                    [
                        f"  - {log[:150]}..." if len(log) > 150 else f"  - {log}"
                        for log in sample_logs
                    ]
                )
                evidence_parts.append(f"Sample Logs:\n{logs_preview}")

        elif source == "logs" and evidence.get("error_logs"):
            error_logs = evidence.get("error_logs", [])[:3]
            logs_json = "\n".join(
                [
                    f"  - {str(log)[:150]}..." if len(str(log)) > 150 else f"  - {str(log)}"
                    for log in error_logs
                ]
            )
            evidence_parts.append(f"Error Logs:\n{logs_json}")

        elif source == "aws_batch_jobs" and evidence.get("failed_jobs"):
            failed_jobs = evidence.get("failed_jobs", [])[:3]
            jobs_json = "\n".join(
                [
                    f"  - {str(job)[:150]}..." if len(str(job)) > 150 else f"  - {str(job)}"
                    for job in failed_jobs
                ]
            )
            evidence_parts.append(f"Failed Jobs:\n{jobs_json}")

        elif source == "tracer_tools" and evidence.get("failed_tools"):
            failed_tools = evidence.get("failed_tools", [])[:3]
            tools_json = "\n".join(
                [
                    f"  - {str(tool)[:150]}..." if len(str(tool)) > 150 else f"  - {str(tool)}"
                    for tool in failed_tools
                ]
            )
            evidence_parts.append(f"Failed Tools:\n{tools_json}")

        elif source == "host_metrics" and evidence.get("host_metrics", {}).get("data"):
            metrics = evidence.get("host_metrics", {}).get("data", {})
            metrics_str = str(metrics)[:200] + "..." if len(str(metrics)) > 200 else str(metrics)
            evidence_parts.append(f"Host Metrics: {metrics_str}")

    if not evidence_parts:
        return ""

    return "\n".join(evidence_parts)


def _collect_cited_sources(ctx: ReportContext, evidence: dict) -> list[str]:
    """Collect all evidence sources that should be cited.

    Args:
        ctx: Report context
        evidence: Evidence dictionary

    Returns:
        List of evidence source identifiers
    """
    sources: list[str] = []

    # Collect sources from validated claims
    for claim_data in ctx.get("validated_claims", []):
        for source in claim_data.get("evidence_sources", []):
            if source not in sources:
                sources.append(source)

    # Add CloudWatch if available
    cw_available = bool(build_cloudwatch_url(ctx) or evidence.get("cloudwatch_logs"))
    if cw_available and "cloudwatch_logs" not in sources:
        sources.append("cloudwatch_logs")

    # Add Lambda evidence
    if evidence.get("lambda_function") and "lambda_function" not in sources:
        sources.append("lambda_function")
    if evidence.get("lambda_logs") and "lambda_logs" not in sources:
        sources.append("lambda_logs")
    if evidence.get("lambda_errors") and "lambda_errors" not in sources:
        sources.append("lambda_errors")

    # Add S3 evidence
    if evidence.get("s3_object") and "s3_object" not in sources:
        sources.append("s3_object")
    if evidence.get("s3_audit_payload") and "s3_audit_payload" not in sources:
        sources.append("s3_audit_payload")

    # Add other evidence
    if evidence.get("error_logs") and "logs" not in sources:
        sources.append("logs")
    if evidence.get("failed_jobs") and "aws_batch_jobs" not in sources:
        sources.append("aws_batch_jobs")
    if evidence.get("failed_tools") and "tracer_tools" not in sources:
        sources.append("tracer_tools")
    if evidence.get("host_metrics", {}).get("data") and "host_metrics" not in sources:
        sources.append("host_metrics")

    # Fallback to generic evidence summary
    if not sources:
        sources.append("evidence_analysis")

    return sources


def _format_source_citations(
    sources: list[str], evidence: dict, ctx: ReportContext, indent_prefix: str = ""
) -> list[str]:
    """Format citations for a list of evidence sources.

    Args:
        sources: List of evidence source identifiers
        evidence: Evidence dictionary
        ctx: Report context
        indent_prefix: Prefix for indentation (e.g., "  ")

    Returns:
        List of formatted citation strings
    """
    source_citations: list[str] = []

    for source in sources:
        label = EVIDENCE_SOURCE_LABELS.get(source, source.replace("_", " ").title())

        # Special handling for CloudWatch logs
        if source == "cloudwatch_logs":
            cw_url = build_cloudwatch_url(ctx)
            if cw_url:
                link = format_slack_link("View CloudWatch evidence", cw_url)
                source_citations.append(f"{indent_prefix}- {link}")
                continue

        # Special handling for Lambda functions - include AWS Console URL
        if source == "lambda_function":
            lambda_func = evidence.get("lambda_function", {})
            function_name = lambda_func.get("function_name")
            if function_name:
                region = ctx.get("cloudwatch_region") or "us-east-1"
                lambda_url = build_lambda_console_url(function_name, region)
                source_citations.append(f"{indent_prefix}- {label}:")
                source_citations.append(format_text_block(lambda_url))

                # Also include function details
                payload = sample_evidence_payload(source, evidence)
                if payload:
                    source_citations.append(format_json_block(format_json_payload(payload)))
                continue

        # Generic evidence payload
        payload = sample_evidence_payload(source, evidence)
        if payload is None:
            continue

        source_citations.append(f"{indent_prefix}- {label}:")
        source_citations.append(format_json_block(format_json_payload(payload)))

    return source_citations


def format_cited_evidence_section(ctx: ReportContext) -> str:
    """Format the cited evidence section of the report.

    Shows evidence sources used to support validated claims, with sample data
    and console URLs where applicable.

    Args:
        ctx: Report context containing claims and evidence

    Returns:
        Formatted evidence section with citations
    """
    evidence = ctx.get("evidence", {})
    citations: list[str] = []

    # Format per-claim citations
    claim_lines: list[str] = []
    for idx, claim_data in enumerate(ctx.get("validated_claims", []), 1):
        claim = claim_data.get("claim", "").strip()
        if not claim:
            continue

        sources = claim_data.get("evidence_sources", [])
        claim_citations = _format_source_citations(sources, evidence, ctx, indent_prefix="  ")

        if not claim_citations:
            continue

        claim_block = [f'{idx}. Claim: "{shorten_text(claim, max_chars=120)}"']
        claim_block.extend(claim_citations)
        claim_lines.append("\n".join(claim_block))

    if claim_lines:
        citations.append("")
        citations.append("\n\n".join(claim_lines))
    else:
        # Fallback: show all available evidence sources
        sources = _collect_cited_sources(ctx, evidence)
        fallback_citations = _format_source_citations(sources, evidence, ctx)
        citations.extend(fallback_citations)

    if not citations:
        return ""

    return "\n*Cited Evidence:*\n" + "\n".join(citations) + "\n"

"""
Publish findings node - generates AND renders the final report.

This is the final node in the pipeline. It:
1. Extracts data from state
2. Formats the report text
3. Renders the report to terminal
4. Sets state.slack_message for external use (Slack, etc.)
"""

from typing import Any, TypedDict

from langsmith import traceable
from rich.console import Console
from rich.panel import Panel

from app.agent.constants import TRACER_DEFAULT_INVESTIGATION_URL
from app.agent.output import get_output_format
from app.agent.state import InvestigationState

# ─────────────────────────────────────────────────────────────────────────────
# Report Context
# ─────────────────────────────────────────────────────────────────────────────


class ReportContext(TypedDict, total=False):
    """Data extracted from state for report formatting."""

    pipeline_name: str
    root_cause: str
    confidence: float
    validated_claims: list[dict]
    non_validated_claims: list[dict]
    validity_score: float
    s3_marker_exists: bool
    tracer_run_status: str | None
    tracer_run_name: str | None
    tracer_pipeline_name: str | None
    tracer_run_cost: float
    tracer_max_ram_gb: float
    tracer_user_email: str | None
    tracer_team: str | None
    tracer_instance_type: str | None
    tracer_failed_tasks: int
    batch_failure_reason: str | None
    batch_failed_jobs: int
    cloudwatch_log_group: str | None
    cloudwatch_log_stream: str | None
    cloudwatch_logs_url: str | None
    evidence: dict  # Raw evidence data for citation


def _build_report_context(state: dict[str, Any]) -> ReportContext:
    """Extract data from state.context and state.evidence for the report formatter."""
    context = state.get("context", {})
    evidence = state.get("evidence", {})
    web_run = context.get("tracer_web_run", {}) or {}
    batch = evidence.get("batch_jobs", {}) or {}
    s3 = evidence.get("s3", {}) or {}
    raw_alert = state.get("raw_alert", {}) or {}

    validated_claims = state.get("validated_claims", [])
    non_validated_claims = state.get("non_validated_claims", [])

    # Filter out junk claims (like "NON_" prefix artifacts)
    validated_claims = [
        c
        for c in validated_claims
        if c.get("claim", "").strip() and not c.get("claim", "").strip().startswith("NON_")
    ]

    # Extract CloudWatch info from alert
    cloudwatch_url = None
    cloudwatch_group = None
    cloudwatch_stream = None
    if isinstance(raw_alert, dict):
        cloudwatch_url = raw_alert.get("cloudwatch_logs_url") or raw_alert.get("cloudwatch_url")
        cloudwatch_group = raw_alert.get("cloudwatch_log_group")
        cloudwatch_stream = raw_alert.get("cloudwatch_log_stream")

    return {
        "pipeline_name": state.get("pipeline_name", "unknown"),
        "root_cause": state.get("root_cause", ""),
        "confidence": state.get("confidence", 0.0),
        "validated_claims": validated_claims,
        "non_validated_claims": non_validated_claims,
        "validity_score": state.get("validity_score", 0.0),
        "s3_marker_exists": s3.get("marker_exists", False),
        "tracer_run_status": web_run.get("status"),
        "tracer_run_name": web_run.get("run_name"),
        "tracer_pipeline_name": web_run.get("pipeline_name"),
        "tracer_run_cost": web_run.get("run_cost", 0),
        "tracer_max_ram_gb": web_run.get("max_ram_gb", 0),
        "tracer_user_email": web_run.get("user_email"),
        "tracer_team": web_run.get("team"),
        "tracer_instance_type": web_run.get("instance_type"),
        "tracer_failed_tasks": len(evidence.get("failed_jobs", [])),
        "batch_failure_reason": batch.get("failure_reason"),
        "batch_failed_jobs": batch.get("failed_jobs", 0),
        "cloudwatch_log_group": cloudwatch_group,
        "cloudwatch_log_stream": cloudwatch_stream,
        "cloudwatch_logs_url": cloudwatch_url,
        "evidence": evidence,  # Include raw evidence for citation
    }


# ─────────────────────────────────────────────────────────────────────────────
# Report Formatting
# ─────────────────────────────────────────────────────────────────────────────


def _format_evidence_for_claim(claim_data: dict, evidence: dict, ctx: ReportContext) -> str:
    """
    Format evidence URLs or JSON for a specific claim.

    Returns a formatted string with evidence links or JSON snippets.
    """
    evidence_sources = claim_data.get("evidence_sources", [])
    if not evidence_sources:
        return ""

    evidence_parts = []

    for source in evidence_sources:
        if source == "cloudwatch_logs":
            cw_url = ctx.get("cloudwatch_logs_url")
            if cw_url:
                evidence_parts.append(f"CloudWatch Logs: {cw_url}")
            elif ctx.get("cloudwatch_log_group"):
                # Build URL if not provided
                region = "us-east-1"
                encoded_group = ctx["cloudwatch_log_group"].replace("/", "$252F")
                encoded_stream = ctx["cloudwatch_log_stream"].replace("/", "$252F") if ctx.get("cloudwatch_log_stream") else ""
                if encoded_stream:
                    url = (
                        f"https://{region}.console.aws.amazon.com/cloudwatch/home"
                        f"?region={region}#logsV2:log-groups/log-group/{encoded_group}"
                        f"/log-events/{encoded_stream}"
                    )
                else:
                    url = (
                        f"https://{region}.console.aws.amazon.com/cloudwatch/home"
                        f"?region={region}#logsV2:log-groups/log-group/{encoded_group}"
                    )
                evidence_parts.append(f"CloudWatch Logs: {url}")
            # Also include sample log entries if available
            cloudwatch_logs = evidence.get("cloudwatch_logs", [])
            if cloudwatch_logs:
                sample_logs = cloudwatch_logs[:3]  # First 3 log entries
                logs_preview = "\n".join([f"  - {log[:150]}..." if len(log) > 150 else f"  - {log}" for log in sample_logs])
                evidence_parts.append(f"Sample Logs:\n{logs_preview}")

        elif source == "logs" and evidence.get("error_logs"):
            error_logs = evidence.get("error_logs", [])[:3]
            logs_json = "\n".join([f"  - {str(log)[:150]}..." if len(str(log)) > 150 else f"  - {str(log)}" for log in error_logs])
            evidence_parts.append(f"Error Logs:\n{logs_json}")

        elif source == "aws_batch_jobs" and evidence.get("failed_jobs"):
            failed_jobs = evidence.get("failed_jobs", [])[:3]
            jobs_json = "\n".join([f"  - {str(job)[:150]}..." if len(str(job)) > 150 else f"  - {str(job)}" for job in failed_jobs])
            evidence_parts.append(f"Failed Jobs:\n{jobs_json}")

        elif source == "tracer_tools" and evidence.get("failed_tools"):
            failed_tools = evidence.get("failed_tools", [])[:3]
            tools_json = "\n".join([f"  - {str(tool)[:150]}..." if len(str(tool)) > 150 else f"  - {str(tool)}" for tool in failed_tools])
            evidence_parts.append(f"Failed Tools:\n{tools_json}")

        elif source == "host_metrics" and evidence.get("host_metrics", {}).get("data"):
            metrics = evidence.get("host_metrics", {}).get("data", {})
            metrics_str = str(metrics)[:200] + "..." if len(str(metrics)) > 200 else str(metrics)
            evidence_parts.append(f"Host Metrics: {metrics_str}")

    if not evidence_parts:
        return ""

    return "\n".join(evidence_parts)


def _render_cloudwatch_link(ctx: ReportContext) -> str:
    """Render CloudWatch logs link if available in alert."""
    cw_url = ctx.get("cloudwatch_logs_url")
    cw_group = ctx.get("cloudwatch_log_group")
    cw_stream = ctx.get("cloudwatch_log_stream")

    if cw_url:
        return f"\n*CloudWatch Logs:*\n{cw_url}\n"
    elif cw_group and cw_stream:
        # Build URL if not provided (default to us-east-1)
        region = "us-east-1"
        encoded_group = cw_group.replace("/", "$252F")
        encoded_stream = cw_stream.replace("/", "$252F")
        url = (
            f"https://{region}.console.aws.amazon.com/cloudwatch/home"
            f"?region={region}#logsV2:log-groups/log-group/{encoded_group}"
            f"/log-events/{encoded_stream}"
        )
        return f"\n*CloudWatch Logs:*\n* Log Group: {cw_group}\n* Log Stream: {cw_stream}\n* View: {url}\n"

    return ""


def _format_slack_message(ctx: ReportContext) -> str:
    """Format the Slack message output."""
    status = ctx.get("tracer_run_status", "unknown")
    is_failed = status.lower() == "failed" if status else False
    status_marker = "[FAILED]" if is_failed else ""

    batch_info = ""
    if ctx.get("batch_failure_reason"):
        batch_info = f"* Failure Reason: {ctx['batch_failure_reason']}\n"

    tracer_link = TRACER_DEFAULT_INVESTIGATION_URL

    validated_claims = ctx.get("validated_claims", [])
    non_validated_claims = ctx.get("non_validated_claims", [])
    validity_score = ctx.get("validity_score", 0.0)

    validated_section = ""
    non_validated_section = ""
    validity_info = ""

    evidence = ctx.get("evidence", {})

    if validated_claims:
        validated_section = "\n*Validated Claims (Supported by Evidence):*\n"
        evidence_section = "\n*Evidence Details:*\n"

        for idx, claim_data in enumerate(validated_claims, 1):
            claim = claim_data.get("claim", "")
            evidence_sources = claim_data.get("evidence_sources", [])
            evidence_str = f" [Evidence: {', '.join(evidence_sources)}]" if evidence_sources else ""
            validated_section += f"• {claim}{evidence_str}\n"

            # Add evidence details for this claim
            evidence_detail = _format_evidence_for_claim(claim_data, evidence, ctx)
            if evidence_detail:
                evidence_section += f"\n{idx}. Evidence for: \"{claim[:80]}{'...' if len(claim) > 80 else ''}\"\n"
                evidence_section += f"{evidence_detail}\n"

        # Only add evidence section if there's actual evidence to show
        if evidence_section.strip() != "*Evidence Details:*":
            validated_section += evidence_section

    if non_validated_claims:
        non_validated_section = "\n*Non-Validated Claims (Inferred):*\n"
        for claim_data in non_validated_claims:
            claim = claim_data.get("claim", "")
            non_validated_section += f"• {claim}\n"

    if validity_score > 0:
        total = len(validated_claims) + len(non_validated_claims)
        validity_info = (
            f"\n*Validity Score:* {validity_score:.0%} ({len(validated_claims)}/{total} validated)\n"
        )

    root_cause_text = ctx.get("root_cause", "")
    if not validated_claims and not non_validated_claims and root_cause_text:
        conclusion_section = f"\n{root_cause_text}\n"
    else:
        # Ensure linebreak between validated and non-validated sections
        separator = "\n" if validated_section and non_validated_section else ""
        conclusion_section = f"{validated_section}{separator}{non_validated_section}{validity_info}"

    total = len(validated_claims) + len(non_validated_claims)
    pipeline_name = ctx.get("tracer_pipeline_name") or ctx.get("pipeline_name", "unknown")
    return f"""[RCA] {pipeline_name} incident
Analyzed by: pipeline-agent

*Conclusion*
{conclusion_section}
*Confidence:* {ctx.get("confidence", 0.0):.0%}
*Validity Score:* {validity_score:.0%} ({len(validated_claims)}/{total} validated)

*Evidence from Tracer*
* Pipeline: {ctx.get("tracer_pipeline_name", "unknown")}
* Run: {ctx.get("tracer_run_name", "unknown")}
* Status: {status} {status_marker}
* User: {ctx.get("tracer_user_email", "unknown")}
* Team: {ctx.get("tracer_team", "unknown")}
* Cost: ${ctx.get("tracer_run_cost", 0):.2f}
* Instance: {ctx.get("tracer_instance_type", "unknown")}
* Max RAM: {ctx.get("tracer_max_ram_gb", 0):.1f} GB
{batch_info}

*View Investigation:*
{tracer_link}
{_render_cloudwatch_link(ctx)}

*Recommended Actions*
1. Review failed job in Tracer dashboard
2. {"Increase memory allocation - job killed due to " + ctx.get("batch_failure_reason", "OOM") if ctx.get("batch_failure_reason") and "memory" in ctx.get("batch_failure_reason", "").lower() else "Check AWS Batch logs for error details"}
3. Rerun pipeline after fixing issues
"""


# ─────────────────────────────────────────────────────────────────────────────
# Report Rendering
# ─────────────────────────────────────────────────────────────────────────────


def _render_report(slack_message: str, confidence: float, validity_score: float) -> None:
    """Render the final report to terminal."""
    fmt = get_output_format()

    if not slack_message:
        if fmt == "rich":
            Console().print("[yellow]No report generated.[/]")
        else:
            print("No report generated.")
        return

    if fmt == "rich":
        console = Console()
        console.print()
        console.print(Panel(slack_message, title="RCA Report", border_style="green"))
        console.print(
            f"\nInvestigation complete. Confidence: {confidence:.0%} | Validity: {validity_score:.0%}"
        )
    else:
        print("\n" + "=" * 60)
        print("RCA REPORT")
        print("=" * 60)
        print(slack_message)
        print("=" * 60)
        print(f"Investigation complete. Confidence: {confidence:.0%} | Validity: {validity_score:.0%}")


# ─────────────────────────────────────────────────────────────────────────────
# Node Entry Point
# ─────────────────────────────────────────────────────────────────────────────


def main(state: InvestigationState) -> dict:
    """
    Generate and render the final report.

    1. Build report context from state
    2. Format the slack message
    3. Render the report to terminal
    4. Return slack_message for external use
    """
    ctx = _build_report_context(state)
    slack_message = _format_slack_message(ctx)

    # Render the report
    _render_report(slack_message, ctx.get("confidence", 0.0), ctx.get("validity_score", 0.0))

    return {"slack_message": slack_message}


@traceable(name="node_publish_findings")
def node_publish_findings(state: InvestigationState) -> dict:
    """LangGraph node wrapper with LangSmith tracking."""
    return main(state)

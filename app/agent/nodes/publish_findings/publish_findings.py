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


def _build_report_context(state: dict[str, Any]) -> ReportContext:
    """Extract data from state.context and state.evidence for the report formatter."""
    context = state.get("context", {})
    evidence = state.get("evidence", {})
    web_run = context.get("tracer_web_run", {}) or {}
    batch = evidence.get("batch_jobs", {}) or {}
    s3 = evidence.get("s3", {}) or {}

    validated_claims = state.get("validated_claims", [])
    non_validated_claims = state.get("non_validated_claims", [])

    # Filter out junk claims (like "NON_" prefix artifacts)
    validated_claims = [
        c
        for c in validated_claims
        if c.get("claim", "").strip() and not c.get("claim", "").strip().startswith("NON_")
    ]

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
    }


# ─────────────────────────────────────────────────────────────────────────────
# Report Formatting
# ─────────────────────────────────────────────────────────────────────────────


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

    if validated_claims:
        validated_section = "\n*Validated Claims (Supported by Evidence):*\n"
        for claim_data in validated_claims:
            claim = claim_data.get("claim", "")
            evidence = claim_data.get("evidence_sources", [])
            evidence_str = f" [Evidence: {', '.join(evidence)}]" if evidence else ""
            validated_section += f"• {claim}{evidence_str}\n"

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
{batch_info}* S3 _SUCCESS marker: {"not found" if not ctx.get("s3_marker_exists") else "present"}

*View Investigation:*
{tracer_link}

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

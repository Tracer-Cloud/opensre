"""Main report formatting and assembly for Slack messages."""

from app.agent.constants import TRACER_DEFAULT_INVESTIGATION_URL
from app.agent.nodes.publish_findings.context.models import ReportContext
from app.agent.nodes.publish_findings.formatters.base import format_slack_link
from app.agent.nodes.publish_findings.formatters.evidence import (
    format_cited_evidence_section,
    format_evidence_for_claim,
)
from app.agent.nodes.publish_findings.formatters.infrastructure import (
    format_infrastructure_correlation,
)
from app.agent.nodes.publish_findings.formatters.lineage import format_data_lineage_flow
from app.agent.nodes.publish_findings.urls.aws import build_cloudwatch_url


def render_cloudwatch_link(ctx: ReportContext) -> str:
    """Render CloudWatch logs link if available in context.

    Args:
        ctx: Report context

    Returns:
        Formatted CloudWatch link section or empty string
    """
    cw_url = ctx.get("cloudwatch_logs_url")
    cw_group = ctx.get("cloudwatch_log_group")
    cw_stream = ctx.get("cloudwatch_log_stream")

    if cw_url:
        return f"\n*CloudWatch Logs:*\n{format_slack_link('View logs', cw_url)}\n"
    elif cw_group and cw_stream:
        # Build URL if not provided
        url = build_cloudwatch_url(ctx)
        view_link = format_slack_link("Open in console", url) if url else url
        return (
            "\n*CloudWatch Logs:*\n"
            f"* Log Group: {cw_group}\n"
            f"* Log Stream: {cw_stream}\n"
            f"* View: {view_link}\n"
        )

    return ""


def _format_validated_claims_section(ctx: ReportContext, evidence: dict) -> str:
    """Format the validated claims section with evidence details.

    Args:
        ctx: Report context
        evidence: Evidence dictionary

    Returns:
        Formatted validated claims section
    """
    validated_claims = ctx.get("validated_claims", [])
    if not validated_claims:
        return ""

    validated_section = "\n*Validated Claims (Supported by Evidence):*\n"
    evidence_section = "\n*Evidence Details:*\n"

    for idx, claim_data in enumerate(validated_claims, 1):
        claim = claim_data.get("claim", "")
        evidence_sources = claim_data.get("evidence_sources", [])
        evidence_str = f" [Evidence: {', '.join(evidence_sources)}]" if evidence_sources else ""
        validated_section += f"• {claim}{evidence_str}\n"

        # Add evidence details for this claim
        evidence_detail = format_evidence_for_claim(claim_data, evidence, ctx)
        if evidence_detail:
            evidence_section += (
                f'\n{idx}. Evidence for: "{claim[:80]}{"..." if len(claim) > 80 else ""}"\n'
            )
            evidence_section += f"{evidence_detail}\n"

    # Only add evidence section if there's actual evidence to show
    if evidence_section.strip() != "*Evidence Details:*":
        validated_section += evidence_section

    return validated_section


def _format_non_validated_claims_section(ctx: ReportContext) -> str:
    """Format the non-validated claims section.

    Args:
        ctx: Report context

    Returns:
        Formatted non-validated claims section
    """
    non_validated_claims = ctx.get("non_validated_claims", [])
    if not non_validated_claims:
        return ""

    non_validated_section = "\n*Non-Validated Claims (Inferred):*\n"
    for claim_data in non_validated_claims:
        claim = claim_data.get("claim", "")
        non_validated_section += f"• {claim}\n"

    return non_validated_section


def _format_validity_info(ctx: ReportContext) -> str:
    """Format the validity score summary.

    Args:
        ctx: Report context

    Returns:
        Formatted validity info line
    """
    validity_score = ctx.get("validity_score", 0.0)
    if validity_score <= 0:
        return ""

    validated_claims = ctx.get("validated_claims", [])
    non_validated_claims = ctx.get("non_validated_claims", [])
    total = len(validated_claims) + len(non_validated_claims)

    return f"\n*Validity Score:* {validity_score:.0%} ({len(validated_claims)}/{total} validated)\n"


def _format_conclusion_section(ctx: ReportContext, evidence: dict) -> str:
    """Format the conclusion section with claims and root cause.

    Args:
        ctx: Report context
        evidence: Evidence dictionary

    Returns:
        Formatted conclusion section
    """
    validated_section = _format_validated_claims_section(ctx, evidence)
    non_validated_section = _format_non_validated_claims_section(ctx)
    validity_info = _format_validity_info(ctx)

    root_cause_text = ctx.get("root_cause", "")

    # If no claims, just show root cause
    if not validated_section and not non_validated_section and root_cause_text:
        return f"\n{root_cause_text}\n"

    # Otherwise, combine claims with proper spacing
    separator = "\n" if validated_section and non_validated_section else ""
    return f"{validated_section}{separator}{non_validated_section}{validity_info}"


def format_slack_message(ctx: ReportContext) -> str:
    """Format the complete Slack message for RCA report.

    Assembles all report sections:
    - Header with pipeline name and alert ID
    - Conclusion with claims and root cause
    - Data lineage flow
    - Investigation trace
    - Confidence and validity scores
    - Cited evidence with samples and URLs
    - Investigation and CloudWatch links

    Args:
        ctx: Report context with all investigation data

    Returns:
        Formatted Slack message string
    """
    evidence = ctx.get("evidence", {})
    validated_claims = ctx.get("validated_claims", [])
    non_validated_claims = ctx.get("non_validated_claims", [])
    validity_score = ctx.get("validity_score", 0.0)

    # Build report sections
    tracer_link = TRACER_DEFAULT_INVESTIGATION_URL
    tracer_cta = f"*{format_slack_link('View Investigation', tracer_link)}*"
    pipeline_name = ctx.get("tracer_pipeline_name") or ctx.get("pipeline_name", "unknown")
    alert_id_str = f"\n*Alert ID:* {ctx['alert_id']}" if ctx.get("alert_id") else ""

    conclusion_section = _format_conclusion_section(ctx, evidence)
    lineage_section = format_data_lineage_flow(ctx)
    infrastructure_section = format_infrastructure_correlation(ctx)
    cited_evidence_section = format_cited_evidence_section(ctx)
    cloudwatch_link = render_cloudwatch_link(ctx)

    total_claims = len(validated_claims) + len(non_validated_claims)
    confidence = ctx.get("confidence", 0.0)

    # Assemble final message
    return f"""[RCA] {pipeline_name} incident
Analyzed by: pipeline-agent
{alert_id_str}

*Conclusion*
{conclusion_section}
{lineage_section}
{infrastructure_section}
*Confidence:* {confidence:.0%}
*Validity Score:* {validity_score:.0%} ({len(validated_claims)}/{total_claims} validated)
{cited_evidence_section}

{tracer_cta}
{cloudwatch_link}
"""

"""Main report formatting and assembly for Slack messages."""

import os
import re

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
from app.agent.utils.auth import extract_org_slug_from_jwt
from app.config import get_tracer_base_url


def get_investigation_url() -> str:
    """Build investigation URL from JWT org_slug and base URL."""
    jwt = os.getenv("JWT_TOKEN")
    slug = extract_org_slug_from_jwt(jwt) if jwt else None
    base = get_tracer_base_url()
    return f"{base}/{slug}/investigations" if slug else f"{base}/investigations"


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
        return f"\n*{format_slack_link('CloudWatch Logs', cw_url)}*\n"
    elif cw_group and cw_stream:
        # Build URL if not provided
        url = build_cloudwatch_url(ctx)
        view_link = format_slack_link("CloudWatch Logs", url) if url else None
        if view_link:
            return f"\n*{view_link}*\n"
        return (
            "\n*CloudWatch Logs:*\n"
            f"* Log Group: {cw_group}\n"
            f"* Log Stream: {cw_stream}\n"
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
    has_catalog = bool(ctx.get("evidence_catalog"))
    catalog = ctx.get("evidence_catalog") or {}

    for idx, claim_data in enumerate(validated_claims, 1):
        claim = claim_data.get("claim", "")
        # Strip legacy inline evidence markers
        claim = re.sub(r"\s*\[(?i:evidence):[^\]]*\]", "", claim).strip()
        evidence_ids = claim_data.get("evidence_ids", [])
        evidence_labels = claim_data.get("evidence_labels", [])
        # Build clickable labels if possible
        evidence_list = []
        if evidence_ids:
            for eid in evidence_ids:
                entry = catalog.get(eid, {})
                disp = entry.get("display_id", eid)
                url = entry.get("url")
                evidence_list.append(format_slack_link(disp, url) if url else disp)
        elif evidence_labels:
            evidence_list = evidence_labels
        # no fallback to sources to avoid duplication
        else:
            evidence_list = []
        evidence_str = f" [Evidence: {', '.join(evidence_list)}]" if evidence_list else ""
        validated_section += f"• {claim}{evidence_str}\n"

        # Add evidence details only when no catalog is present (fallback)
        if not has_catalog:
            evidence_detail = format_evidence_for_claim(claim_data, evidence, ctx)
            if evidence_detail:
                evidence_section += (
                    f'\n{idx}. Evidence for: "{claim[:80]}{"..." if len(claim) > 80 else ""}"\n'
                )
                evidence_section += f"{evidence_detail}\n"

    # Only add evidence section if there's actual evidence to show (and no catalog)
    if not has_catalog and evidence_section.strip() != "*Evidence Details:*":
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


def _format_recommendations(ctx: ReportContext) -> str:
    """Render investigation recommendations, if any."""
    recs = ctx.get("investigation_recommendations", []) or []
    if not recs:
        return ""
    lines = ["*Suggested Next Steps:*"]
    for rec in recs:
        if rec:
            lines.append(f"• {rec}")
    return "\n" + "\n".join(lines) + "\n"


def _format_remediation_steps(ctx: ReportContext) -> str:
    """Render remediation/prevention steps, if any."""
    steps = ctx.get("remediation_steps", []) or []
    if not steps:
        return ""
    lines = ["*Remediation Next Steps:*"]
    for step in steps:
        if step:
            lines.append(f"• {step}")
    return "\n" + "\n".join(lines) + "\n"


def _first_sentence(text: str) -> str:
    """Return the first sentence from text, normalized to one line."""
    normalized = " ".join(text.split())
    if not normalized:
        return ""

    parts = re.split(r"(?<=[.?!])\s+", normalized, maxsplit=1)
    sentence = parts[0]
    sentence = sentence.rstrip(".?!")
    return sentence


def _is_speculative(text: str) -> bool:
    speculative_terms = (" may ", " might ", " possibly", " possible ", " likely ")
    lower = f" {text.lower()} "
    return any(term in lower for term in speculative_terms)


def _derive_root_cause_sentence(ctx: ReportContext) -> str:
    """Derive a concise, single-sentence root cause with causal preference."""
    root_cause_text = ctx.get("root_cause", "") or ""
    validated_claims = ctx.get("validated_claims", [])

    if root_cause_text:
        sentence = _first_sentence(root_cause_text)
        if sentence and not _is_speculative(sentence):
            return sentence

    causal_connectors = (
        " because ",
        " due to ",
        " caused ",
        " resulted in ",
        " led to ",
        " root cause ",
        " failure triggered ",
    )

    for claim_data in validated_claims:
        claim = claim_data.get("claim", "") or ""
        lower = f" {claim.lower()} "
        if any(connector in lower for connector in causal_connectors):
            sentence = _first_sentence(claim)
            if sentence:
                return _first_sentence(_remove_speculative_words(sentence))

    return ""


def _remove_speculative_words(text: str) -> str:
    speculative = ("may", "might", "likely", "probably", "possibly")
    words = text.split()
    filtered = [w for w in words if w.lower() not in speculative]
    return " ".join(filtered)


def _format_conclusion_section(ctx: ReportContext, evidence: dict) -> str:
    validated_section = _format_validated_claims_section(ctx, evidence)
    non_validated_section = _format_non_validated_claims_section(ctx)
    validity_info = _format_validity_info(ctx)

    # 1) Always show a one-liner root cause (with a safe fallback)
    root_cause_sentence = _derive_root_cause_sentence(ctx)
    if not root_cause_sentence:
        root_cause_sentence = "Not determined (insufficient evidence)."

    root_cause_block = f"*Root Cause:* {root_cause_sentence}\n"

    # 2) Then add claims (progressive disclosure)
    separator = "\n" if validated_section and non_validated_section else ""
    recommendations_section = _format_recommendations(ctx)
    remediation_section = _format_remediation_steps(ctx)

    claims_block = f"{validated_section}{separator}{non_validated_section}{validity_info}{recommendations_section}{remediation_section}".strip()

    if claims_block:
        return f"\n{root_cause_block}{claims_block}\n"

    return f"\n{root_cause_block}\n"



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
    tracer_link = get_investigation_url()
    tracer_cta = f"*{format_slack_link('View Investigation', tracer_link)}*"
    pipeline_name = ctx.get("tracer_pipeline_name") or ctx.get("pipeline_name", "unknown")
    alert_id_str = f"\n*Alert ID:* {ctx['alert_id']}" if ctx.get("alert_id") else ""
    duration_seconds = ctx.get("investigation_duration_seconds")
    timing_line = (
        f"Timing: {duration_seconds}s" if duration_seconds is not None else "Timing: unknown"
    )

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
{timing_line}
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

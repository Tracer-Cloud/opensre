"""Main report formatting and assembly for Slack messages."""

import os
import re

from app.agent.nodes.publish_findings.context.models import ReportContext
from app.agent.nodes.publish_findings.formatters.base import format_slack_link
from app.agent.nodes.publish_findings.formatters.causal_chain import format_causal_chain_section
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


def _sanitize_for_slack(text: str) -> str:
    """Convert markdown formatting to Slack mrkdwn.

    Slack does not render # headers, ** bold, or other standard markdown.
    This converts common patterns to Slack-native formatting.
    """
    # Strip markdown headers (# ## ### etc.) → *bold*
    result = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    # Convert **bold** to *bold*
    result = re.sub(r"\*\*(.+?)\*\*", r"*\1*", result)
    # Convert __bold__ to *bold*
    result = re.sub(r"__(.+?)__", r"*\1*", result)
    return result


def _first_sentence(text: str) -> str:
    """Return the first sentence from text, normalized to one line.

    Strips markdown headers and structural labels before extracting.
    """
    # Remove markdown headers (at line start or inline after collapse)
    cleaned = re.sub(r"(?:^|\s)#{1,6}\s+", " ", text, flags=re.MULTILINE)
    # Remove structural labels like "Problem Statement", "Summary", "Context"
    cleaned = re.sub(
        r"\b(?:Problem Statement|Summary|Context|Description|Overview)\b[:\s]*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    normalized = " ".join(cleaned.split()).strip()
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

    # 1) Always show a one-liner root cause (with a safe fallback)
    root_cause_sentence = _derive_root_cause_sentence(ctx)
    if not root_cause_sentence:
        root_cause_sentence = "Not determined (insufficient evidence)."

    root_cause_block = f"*Root Cause:* {root_cause_sentence}\n"

    # 2) Then add claims (progressive disclosure)
    separator = "\n" if validated_section and non_validated_section else ""

    claims_block = f"{validated_section}{separator}{non_validated_section}".strip()

    if claims_block:
        return f"\n{root_cause_block}{claims_block}\n"

    return f"\n{root_cause_block}\n"



def format_slack_message(ctx: ReportContext) -> str:
    """Format a plain-text Slack message for the RCA report.

    Used as the `text` fallback (notifications, accessibility) when
    Block Kit blocks are the primary rendered content.

    Args:
        ctx: Report context with all investigation data

    Returns:
        Formatted Slack message string (plain-text with mrkdwn)
    """
    evidence = ctx.get("evidence", {})

    pipeline_name = ctx.get("tracer_pipeline_name") or ctx.get("pipeline_name", "unknown")
    alert_id = ctx.get("alert_id")
    duration_seconds = ctx.get("investigation_duration_seconds")

    conclusion_section = _sanitize_for_slack(_format_conclusion_section(ctx, evidence))
    lineage_section = _sanitize_for_slack(format_data_lineage_flow(ctx))
    infrastructure_section = _sanitize_for_slack(format_infrastructure_correlation(ctx))
    cited_evidence_section = _sanitize_for_slack(format_cited_evidence_section(ctx))
    cloudwatch_link = render_cloudwatch_link(ctx)
    causal_chain_section = _sanitize_for_slack(format_causal_chain_section(ctx.get("causal_chain")))

    meta_lines = []
    if duration_seconds is not None:
        meta_lines.append(f"Timing: {duration_seconds}s")
    if alert_id:
        meta_lines.append(f"*Alert ID:* {alert_id}")
    meta_block = "\n" + "\n".join(meta_lines) if meta_lines else ""

    return f"""[RCA] {pipeline_name} incident
{conclusion_section}
{causal_chain_section}
{lineage_section}
{infrastructure_section}
{cited_evidence_section}
{cloudwatch_link}{meta_block}
"""


def build_slack_blocks(ctx: ReportContext) -> list[dict]:
    """Build Slack Block Kit blocks for the RCA report.

    Produces a clean, well-structured message using Slack's native
    formatting: header, sections with mrkdwn, dividers, and context blocks.

    Args:
        ctx: Report context with all investigation data

    Returns:
        List of Block Kit block dicts.
    """
    from typing import Any

    validated_claims = ctx.get("validated_claims", [])
    non_validated_claims = ctx.get("non_validated_claims", [])

    pipeline_name = ctx.get("tracer_pipeline_name") or ctx.get("pipeline_name", "unknown")
    duration_seconds = ctx.get("investigation_duration_seconds")
    alert_id = ctx.get("alert_id")

    blocks: list[dict[str, Any]] = []

    # ── Header ──
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": f"\U0001f6a8 [RCA] {pipeline_name} incident"},
    })

    def _mrkdwn_section(text: str) -> dict[str, Any]:
        """Build a section block with sanitized mrkdwn text.

        Slack section blocks have a 3000 char limit per text field.
        """
        sanitized = _sanitize_for_slack(text)
        if len(sanitized) > 2990:
            sanitized = sanitized[:2987] + "..."
        return {
            "type": "section",
            "text": {"type": "mrkdwn", "text": sanitized},
        }

    # ── Root Cause ──
    root_cause_sentence = _derive_root_cause_sentence(ctx)
    if not root_cause_sentence:
        root_cause_sentence = "Not determined (insufficient evidence)"
    blocks.append(_mrkdwn_section(f"*Root Cause*\n{root_cause_sentence}"))

    # ── Validated Claims ──
    if validated_claims:
        catalog = ctx.get("evidence_catalog") or {}
        claims_lines = []
        for claim_data in validated_claims:
            claim = claim_data.get("claim", "")
            claim = re.sub(r"\s*\[(?i:evidence):[^\]]*\]", "", claim).strip()
            claim = _sanitize_for_slack(claim)
            evidence_ids = claim_data.get("evidence_ids", [])
            evidence_list = []
            if evidence_ids:
                for eid in evidence_ids:
                    entry = catalog.get(eid, {})
                    disp = entry.get("display_id", eid)
                    url = entry.get("url")
                    evidence_list.append(format_slack_link(disp, url) if url else disp)
            ev_str = f" [{', '.join(evidence_list)}]" if evidence_list else ""
            claims_lines.append(f"\u2022 {claim}{ev_str}")
        blocks.append(_mrkdwn_section("*Findings*\n" + "\n".join(claims_lines)))

    # ── Non-Validated Claims ──
    if non_validated_claims:
        nv_lines = []
        for claim_data in non_validated_claims:
            claim = _sanitize_for_slack(claim_data.get("claim", ""))
            nv_lines.append(f"\u2022 {claim}")
        blocks.append(_mrkdwn_section("*Inferred (not yet validated)*\n" + "\n".join(nv_lines)))

    # ── Data Lineage ──
    lineage_section = format_data_lineage_flow(ctx).strip()
    if lineage_section:
        blocks.append({"type": "divider"})
        blocks.append(_mrkdwn_section(lineage_section))

    # ── Investigation Trace ──
    infra_section = format_infrastructure_correlation(ctx).strip()
    if infra_section:
        blocks.append(_mrkdwn_section(infra_section))

    # ── Cited Evidence ──
    cited_section = format_cited_evidence_section(ctx).strip()
    if cited_section:
        blocks.append({"type": "divider"})
        blocks.append(_mrkdwn_section(cited_section))

    # ── CloudWatch link ──
    cw_link = render_cloudwatch_link(ctx).strip()
    if cw_link:
        blocks.append(_mrkdwn_section(cw_link))

    # ── Meta context (duration / alert) at the bottom ──
    meta_parts = []
    if duration_seconds is not None:
        meta_parts.append(f"Analyzed in {duration_seconds}s")
    if alert_id:
        meta_parts.append(f"Alert: {alert_id}")
    if meta_parts:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": " | ".join(meta_parts)}],
        })

    return blocks

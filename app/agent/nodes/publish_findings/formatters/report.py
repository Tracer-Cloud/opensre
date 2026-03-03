"""Main report formatting and assembly for Slack messages."""

import re

from app.agent.nodes.publish_findings.formatters.base import format_slack_link
from app.agent.nodes.publish_findings.formatters.evidence import (
    format_cited_evidence_section,
)
from app.agent.nodes.publish_findings.formatters.infrastructure import (
    format_infrastructure_correlation,
    format_pod_line,
    get_failed_pods,
)
from app.agent.nodes.publish_findings.formatters.lineage import format_data_lineage_flow
from app.agent.nodes.publish_findings.report_context import ReportContext
from app.agent.nodes.publish_findings.urls.aws import build_cloudwatch_url
from app.config import get_tracer_base_url


def get_investigation_url(org_slug: str | None = None) -> str:
    """Build investigation URL using the organization slug from state."""
    base = get_tracer_base_url()
    return f"{base}/{org_slug}/investigations" if org_slug else f"{base}/investigations"


def render_cloudwatch_link(ctx: ReportContext) -> str:
    """Render CloudWatch logs link if available in context."""
    cw_url = ctx.get("cloudwatch_logs_url")
    cw_group = ctx.get("cloudwatch_log_group")
    cw_stream = ctx.get("cloudwatch_log_stream")

    if cw_url:
        return f"\n*{format_slack_link('CloudWatch Logs', cw_url)}*\n"
    elif cw_group and cw_stream:
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


# ---------------------------------------------------------------------------
# Shared section helpers — called by both text and block renderers
# ---------------------------------------------------------------------------

def _render_claim_lines(ctx: ReportContext) -> tuple[list[str], list[str]]:
    """Return (validated_lines, non_validated_lines) as plain mrkdwn bullet strings.

    Each validated line includes evidence citations like [E1, E2]. Both renderers
    (format_slack_message and build_slack_blocks) call this to avoid duplicating
    the catalog-lookup and link-formatting logic.
    """
    catalog = ctx.get("evidence_catalog") or {}
    evidence = ctx.get("evidence") or {}

    validated_lines: list[str] = []
    for claim_data in ctx.get("validated_claims", []):
        claim = claim_data.get("claim", "")
        claim = _resolve_evidence_tags(claim, evidence)
        claim = _sanitize_for_slack(claim)
        evidence_ids = claim_data.get("evidence_ids", [])
        evidence_labels = claim_data.get("evidence_labels", [])
        evidence_list: list[str] = []
        if evidence_ids:
            for eid in evidence_ids:
                entry = catalog.get(eid, {})
                disp = entry.get("display_id", eid)
                url = entry.get("url")
                evidence_list.append(format_slack_link(disp, url) if url else disp)
        elif evidence_labels:
            evidence_list = list(evidence_labels)
        ev_str = f" [{', '.join(evidence_list)}]" if evidence_list else ""
        validated_lines.append(f"\u2022 {claim}{ev_str}")

    non_validated_lines: list[str] = [
        f"\u2022 {_sanitize_for_slack(cd.get('claim', ''))}"
        for cd in ctx.get("non_validated_claims", [])
    ]

    return validated_lines, non_validated_lines


def _sanitize_for_slack(text: str) -> str:
    """Convert markdown formatting to Slack mrkdwn.

    Slack does not render # headers, ** bold, or other standard markdown.
    This converts common patterns to Slack-native formatting.
    """
    result = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    result = re.sub(r"\*\*(.+?)\*\*", r"*\1*", result)
    result = re.sub(r"__(.+?)__", r"*\1*", result)
    return result


def _mrkdwn_section(text: str) -> "dict | None":
    """Build a Slack Block Kit section block with sanitized mrkdwn text.

    Slack section blocks have a 3000 char limit per text field.
    Returns None when text is empty — caller must skip None results.
    """
    sanitized = _sanitize_for_slack(text).strip()
    if not sanitized:
        return None
    if len(sanitized) > 2990:
        sanitized = sanitized[:2987] + "..."
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": sanitized},
    }


# ---------------------------------------------------------------------------
# Evidence tag resolution helpers
# ---------------------------------------------------------------------------

# Maps LLM source name → ordered list of evidence dict keys to try for a log message
_EVIDENCE_LOG_KEYS: dict[str, list[str]] = {
    "datadog_logs":    ["datadog_error_logs", "datadog_logs"],
    "datadog":         ["datadog_error_logs", "datadog_logs"],
    "grafana_logs":    ["grafana_error_logs", "grafana_logs"],
    "grafana":         ["grafana_error_logs", "grafana_logs"],
    "cloudwatch_logs": ["cloudwatch_logs"],
    "cloudwatch":      ["cloudwatch_logs"],
}


def _resolve_evidence_tags(text: str, evidence: dict) -> str:
    """Replace [evidence: source] tags with the actual log message in a code span.

    Tries error logs first, then all logs for the named source. If no message
    is found the tag is removed silently to avoid leaking raw LLM annotations.
    """
    def _replace(m: re.Match) -> str:
        source = m.group(1).strip().lower()
        for key in _EVIDENCE_LOG_KEYS.get(source, [source]):
            logs = evidence.get(key) or []
            if logs:
                msg = (logs[0].get("message") or "").strip()
                if msg:
                    return f": `{msg}`"
        return ""

    return re.sub(r"\s*\[(?i:evidence):\s*([^\]]+)\]", _replace, text).strip()


def _get_top_error_log(evidence: dict) -> str | None:
    """Return the first error log message from available evidence sources."""
    for key in ("datadog_error_logs", "datadog_logs", "grafana_error_logs", "grafana_logs", "cloudwatch_logs"):
        logs = evidence.get(key) or []
        if logs:
            msg = (logs[0].get("message") or "").strip()
            if msg:
                return msg
    return None


# ---------------------------------------------------------------------------
# Root cause derivation helpers
# ---------------------------------------------------------------------------

def _first_sentence(text: str) -> str:
    """Return the first sentence from text, normalized to one line."""
    cleaned = re.sub(r"(?:^|\s)#{1,6}\s+", " ", text, flags=re.MULTILINE)
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


def _remove_speculative_words(text: str) -> str:
    speculative = ("may", "might", "likely", "probably", "possibly")
    words = text.split()
    filtered = [w for w in words if w.lower() not in speculative]
    return " ".join(filtered)


def _derive_root_cause_sentence(ctx: ReportContext) -> str:
    """Derive a concise, single-sentence root cause with causal preference."""
    root_cause_text = ctx.get("root_cause", "") or ""
    root_cause_text = re.sub(r"\s*\[(?i:evidence):[^\]]*\]", "", root_cause_text).strip()
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
        claim = re.sub(r"\s*\[(?i:evidence):[^\]]*\]", "", claim).strip()
        lower = f" {claim.lower()} "
        if any(connector in lower for connector in causal_connectors):
            sentence = _first_sentence(claim)
            if sentence:
                return _first_sentence(_remove_speculative_words(sentence))

    if root_cause_text:
        sentence = _first_sentence(root_cause_text)
        if sentence:
            return sentence

    if validated_claims:
        claim = validated_claims[0].get("claim", "") or ""
        claim = re.sub(r"\s*\[(?i:evidence):[^\]]*\]", "", claim).strip()
        sentence = _first_sentence(claim)
        if sentence:
            return sentence

    return ""


def _compress_for_title(sentence: str) -> str:
    """Extract a short, punchy title phrase from a root cause sentence."""
    s = re.sub(
        r"^(?:most likely|likely|probably|possibly|it appears(?: that)?|this suggests(?: that)?)\s+",
        "",
        sentence,
        flags=re.IGNORECASE,
    ).strip()
    for connector in (" because ", " due to ", ", but ", " where ", ", causing ", ": "):
        idx = s.lower().find(connector)
        if idx != -1:
            candidate = s[idx + len(connector):].strip()
            if len(candidate) >= 10:
                s = candidate
                break
    for stop in (",", ";"):
        idx = s.find(stop)
        if idx > 15:
            s = s[:idx].strip()
            break
    if s:
        s = s[0].upper() + s[1:]
    if len(s) > 150:
        s = s[:147].rsplit(" ", 1)[0].rstrip(" ,;") + "..."
    return s


def _build_report_title(
    pipeline_name: str, alert_name: str | None, root_cause_sentence: str = ""
) -> str:
    """Build a descriptive report title.

    Priority:
    1. Compressed root cause phrase (most specific — the actual error)
    2. Alert name (stripped of brackets/pipeline prefix)
    3. "{pipeline_name} incident" (generic fallback)
    """
    if root_cause_sentence:
        compressed = _compress_for_title(root_cause_sentence)
        if compressed:
            return f"{pipeline_name}: {compressed}"

    if alert_name and alert_name.lower() not in ("unknown", "unknown alert", ""):
        clean = re.sub(r"^\[.*?\]\s*", "", alert_name).strip()
        if clean:
            return f"{pipeline_name}: {clean}"

    return f"{pipeline_name} incident"


# ---------------------------------------------------------------------------
# Text renderer (Slack mrkdwn fallback + terminal + ingest report_md)
# ---------------------------------------------------------------------------

def format_slack_message(ctx: ReportContext) -> str:
    """Format a plain-text Slack message for the RCA report.

    Used as the `text` fallback (notifications, accessibility, terminal, ingest)
    when Block Kit blocks are the primary rendered content.
    """
    pipeline_name = ctx.get("tracer_pipeline_name") or ctx.get("pipeline_name", "unknown")
    alert_name = ctx.get("alert_name")
    alert_id = ctx.get("alert_id")
    duration_seconds = ctx.get("investigation_duration_seconds")
    root_cause_sentence = _derive_root_cause_sentence(ctx)
    report_title = _build_report_title(pipeline_name, alert_name, root_cause_sentence)

    if not root_cause_sentence:
        root_cause_sentence = "Not determined (insufficient evidence)."
    conclusion_block = f"*Root Cause:* {root_cause_sentence}\n"
    top_log = _get_top_error_log(ctx.get("evidence") or {})
    if top_log:
        conclusion_block += f"`{top_log}`\n"

    validated_lines, non_validated_lines = _render_claim_lines(ctx)
    if validated_lines:
        conclusion_block += "\n*Validated Claims (Supported by Evidence):*\n" + "\n".join(validated_lines) + "\n"
    if non_validated_lines:
        conclusion_block += "\n*Non-Validated Claims (Inferred):*\n" + "\n".join(non_validated_lines) + "\n"

    lineage_section = _sanitize_for_slack(format_data_lineage_flow(ctx))
    infrastructure_section = _sanitize_for_slack(format_infrastructure_correlation(ctx))
    cited_evidence_section = _sanitize_for_slack(format_cited_evidence_section(ctx))
    cloudwatch_link = render_cloudwatch_link(ctx)
    meta_lines = []
    if duration_seconds is not None:
        meta_lines.append(f"Timing: {duration_seconds}s")
    if alert_id:
        meta_lines.append(f"*Alert ID:* {alert_id}")
    meta_block = "\n" + "\n".join(meta_lines) if meta_lines else ""

    return f"""[RCA] {report_title}

{conclusion_block}
{lineage_section}
{infrastructure_section}
{cited_evidence_section}
{cloudwatch_link}{meta_block}
"""


# ---------------------------------------------------------------------------
# Block Kit renderer (Slack interactive cards)
# ---------------------------------------------------------------------------

def build_slack_blocks(ctx: ReportContext) -> list[dict]:
    """Build Slack Block Kit blocks for the RCA report.

    Produces a clean, well-structured message using Slack's native
    formatting: header, sections with mrkdwn, dividers, and context blocks.
    """
    from typing import Any

    pipeline_name = ctx.get("tracer_pipeline_name") or ctx.get("pipeline_name", "unknown")
    alert_name = ctx.get("alert_name")
    duration_seconds = ctx.get("investigation_duration_seconds")
    alert_id = ctx.get("alert_id")
    root_cause_sentence = _derive_root_cause_sentence(ctx)
    report_title = _build_report_title(pipeline_name, alert_name, root_cause_sentence)

    blocks: list[dict[str, Any]] = []

    def _add(block: "dict[str, Any] | None") -> None:
        if block is not None:
            blocks.append(block)

    header_text = f"\U0001f6a8 [RCA] {report_title}"
    if len(header_text) > 150:
        header_text = header_text[:147] + "..."
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": header_text},
    })

    # ── Root Cause ──
    if not root_cause_sentence:
        root_cause_sentence = "Not determined (insufficient evidence)"
    rc_text = f"*Root Cause*\n{root_cause_sentence}"
    top_log = _get_top_error_log(ctx.get("evidence") or {})
    if top_log:
        rc_text += f"\n`{top_log}`"
    _add(_mrkdwn_section(rc_text))

    # ── Failed Pods ──
    datadog_site = ctx.get("datadog_site", "datadoghq.com")
    all_pods = get_failed_pods(ctx)
    pod_lines = [line for p in all_pods[:5] if (line := format_pod_line(p, datadog_site, bullet="\u2022 "))]
    if len(all_pods) > 5:
        pod_lines.append(f"• ... and {len(all_pods) - 5} more pods")
    if pod_lines:
        _add(_mrkdwn_section("*Failed Pods*\n" + "\n".join(pod_lines)))

    # ── Validated Claims (Findings) and Non-Validated Claims ──
    validated_lines, non_validated_lines = _render_claim_lines(ctx)
    if validated_lines:
        _add(_mrkdwn_section("*Findings*\n" + "\n".join(validated_lines)))
    if non_validated_lines:
        _add(_mrkdwn_section("*Inferred (not yet validated)*\n" + "\n".join(non_validated_lines)))

    # ── Data Lineage ──
    lineage_section = format_data_lineage_flow(ctx).strip()
    if lineage_section:
        blocks.append({"type": "divider"})
        _add(_mrkdwn_section(lineage_section))

    # ── Investigation Trace ──
    infra_section = format_infrastructure_correlation(ctx).strip()
    if infra_section:
        _add(_mrkdwn_section(infra_section))

    # ── Cited Evidence ──
    cited_section = format_cited_evidence_section(ctx).strip()
    if cited_section:
        blocks.append({"type": "divider"})
        _add(_mrkdwn_section(cited_section))

    # ── CloudWatch link ──
    cw_link = render_cloudwatch_link(ctx).strip()
    if cw_link:
        _add(_mrkdwn_section(cw_link))

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

    # Slack hard-limits messages to 50 blocks — truncate from the middle to keep
    # the header (first block) and meta/actions (last 2 blocks) intact.
    if len(blocks) > 50:
        blocks = blocks[:48] + blocks[-2:]

    return blocks

"""Data lineage flow formatting for RCA reports."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.nodes.publish_findings.report_context import ReportContext


def _extract_annotations(raw_alert: dict) -> dict[str, Any]:
    """Extract annotations from raw alert."""
    if not isinstance(raw_alert, dict):
        return {}

    annotations = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {}) or {}

    # Try first alert if no top-level annotations
    if not annotations and raw_alert.get("alerts"):
        first_alert = raw_alert.get("alerts", [{}])[0]
        if isinstance(first_alert, dict):
            annotations = first_alert.get("annotations", {}) or {}

    return annotations


def format_data_lineage_flow(ctx: ReportContext) -> str:
    """
    Render Slack-friendly data lineage with concise evidence references.

    Design goals:
    - Evidence-driven: NEVER render a step unless it is directly supported by evidence.
    - Mechanically traceable: each step points to ONE primary evidence item (link + E-id).
    - Scannable: under 10 seconds.
    """
    evidence = ctx.get("evidence", {}) or {}
    raw_alert = ctx.get("raw_alert", {}) or {}
    annotations = _extract_annotations(raw_alert)
    catalog: dict[str, dict] = ctx.get("evidence_catalog") or {}

    # ----------------------------
    # Helpers
    # ----------------------------
    def _slack_link(url: str | None, text: str) -> str:
        """Slack link formatting: <url|text>."""
        return f"<{url}|{text}>" if url else text

    def _entry_url(entry: dict) -> str | None:
        """
        Normalize URL fields across different catalog shapes.
        Keeps this permissive so links don't "disappear" if a key changes.
        """
        return (
            entry.get("url")
            or entry.get("link")
            or entry.get("href")
            or entry.get("console_url")
            or entry.get("display_url")
        )

    def _matches_any(haystack: str, needles: Iterable[str]) -> bool:
        hay = (haystack or "").lower()
        return any(n.lower() in hay for n in needles)

    def _find_best_evidence(keys: list[str]) -> tuple[str | None, str | None, str | None]:
        """
        Return (display_id, label, url) for the best matching evidence entry.
        Priority:
          1) match against `kind` if present
          2) match against eid/label substring
        """
        if not catalog:
            return None, None, None

        keys_l = [k.lower() for k in keys]

        # 1) Prefer explicit kind matches
        for eid, entry in catalog.items():
            kind = (entry.get("kind") or "").lower()
            if kind and any(k in kind for k in keys_l):
                display_id = entry.get("display_id", eid)
                label = entry.get("label") or eid
                url = _entry_url(entry)
                return display_id, label, url

        # 2) Fallback substring match against eid/label
        for eid, entry in catalog.items():
            label = (entry.get("label") or "").lower()
            if _matches_any(eid, keys_l) or _matches_any(label, keys_l):
                display_id = entry.get("display_id", eid)
                pretty_label = entry.get("label") or eid
                url = _entry_url(entry)
                return display_id, pretty_label, url

        return None, None, None

    def _format_evidence_line(keys: list[str]) -> str | None:
        """
        Returns:
          - Evidence: <url|Label> (E1)
        Or None if no evidence exists (caller should omit the step).
        """
        eid, label, url = _find_best_evidence(keys)
        if not (eid and label):
            return None
        return f"- Evidence: {_slack_link(url, label)} ({eid})"

    def _cloudwatch_text() -> str:
        """Best-effort extraction of CloudWatch log snippet text."""
        cw = evidence.get("cloudwatch_logs")
        if isinstance(cw, dict):
            return str(cw.get("snippet") or cw.get("text") or cw.get("message") or "")
        if isinstance(cw, str):
            return cw
        return ""

    def _add_step(
        steps: list[tuple[str, str, str]], title: str, outcome: str, ev_line: str | None
    ) -> None:
        """Append a step only if evidence exists."""
        if not ev_line:
            return
        steps.append((title, outcome, ev_line))

    # ----------------------------
    # Build steps (ONLY if evidence exists)
    # ----------------------------
    steps: list[tuple[str, str, str]] = []

    # 1) External API / Upstream audit
    # Note: only include if we actually have audit evidence (vendor_audit or s3_audit).
    ev_api = _format_evidence_line(["vendor_audit", "s3_audit", "audit payload"])
    _add_step(
        steps,
        "External API",
        "Upstream audit captured; indicates a schema/config change upstream.",
        ev_api,
    )

    # 2) S3 Landing
    # Include only if we have landing evidence (S3 metadata/object metadata entry).
    # Do NOT drive this step from `s3_obj.get('found')` alone, unless you also have an evidence object for it.
    ev_s3 = _format_evidence_line(["s3_metadata", "s3 object metadata", "landing"])
    if ev_s3:
        # If we have evidence entry, we can safely say ingestion captured.
        landing_outcome = "Landing object captured; payload stored with schema metadata present."
        # If your evidence object contains schema_version / flags, you could enrich this outcome safely here.
        # Keep it short.
        _add_step(steps, "S3 Landing", landing_outcome, ev_s3)

    # 3) Prefect Flow / CloudWatch
    cw_text = _cloudwatch_text()
    ev_cw = _format_evidence_line(["cloudwatch", "prefect", "ecs"])
    # We allow either a catalog evidence link OR actual log text.
    has_cw_signal = bool(ev_cw) or bool(cw_text.strip())

    if has_cw_signal:
        init_only = (
            "starting prefect server" in cw_text.lower()
            or "waiting for server to initialize" in cw_text.lower()
        )
        outcome = (
            "Only Prefect server initialization captured; flow execution logs missing."
            if init_only
            else "Prefect execution logs captured; review for the first error/failure."
        )
        # If there is no catalog link but we have text, we still want the step — but it must remain traceable.
        # In that case, fall back to a non-clickable evidence label.
        if not ev_cw:
            ev_cw = "- Evidence: CloudWatch Logs (captured)"
        _add_step(steps, "Prefect Flow", outcome, ev_cw)

    # 4) S3 Processed (optional)
    # Only include when we can mechanically assert "missing": processed_marker_exists is explicitly False.
    processed_bucket = annotations.get("processed_bucket")
    processed_marker_exists = evidence.get("s3", {}).get("processed_marker_exists")

    if processed_bucket and processed_marker_exists is False:
        ev_processed = _format_evidence_line(["s3_processed", "processed", "marker"])
        # Even if we don't have a link, we can still include this only if the False is evidence-backed elsewhere.
        # Prefer linking; but don't pretend.
        if not ev_processed:
            ev_processed = "- Evidence: Processed marker check (explicitly absent)"
        _add_step(
            steps,
            "S3 Processed",
            "Expected processed output is missing (marker explicitly absent).",
            ev_processed,
        )

    if not steps:
        # If we cannot prove any steps, don't show a fake chain.
        return ""

    # ----------------------------
    # Render
    # ----------------------------
    lines: list[str] = ["*Data Lineage (Evidence-Based)*", ""]
    for idx, (system, outcome, ev_line) in enumerate(steps):
        lines.append(system)
        lines.append(f"- {outcome}")
        lines.append(ev_line)
        if idx < len(steps) - 1:
            lines.append("↓")

    return "\n".join(lines).rstrip() + "\n"

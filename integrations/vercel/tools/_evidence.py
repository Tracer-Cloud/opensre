"""Evidence mappers for vercel_deployment_status and vercel_deployment_logs."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry

#: Vercel's /v6/deployments endpoint caps limit at 100 server-side
#: (min(limit, 100) in integrations/vercel/client.py) and surfaces no
#: pagination metadata -- a returned count cannot be distinguished from a
#: true total except by comparing it against the effective page size.
_VERCEL_MAX_PAGE_SIZE = 100

#: get_deployment_events/get_runtime_logs both cap limit at 2000 server-side
#: and share the tool's single `limit` param, with no pagination metadata
#: surfaced -- a returned count cannot be distinguished from a true total
#: except by comparing it against the effective page size.
_VERCEL_LOGS_MAX_PAGE_SIZE = 2000


def _vercel_page_is_truncated(returned_count: int, requested_limit: int) -> bool:
    effective_limit = min(max(requested_limit, 1), _VERCEL_MAX_PAGE_SIZE)
    return returned_count >= effective_limit


def _vercel_logs_page_is_truncated(returned_count: int, requested_limit: int) -> bool:
    effective_limit = min(max(requested_limit, 1), _VERCEL_LOGS_MAX_PAGE_SIZE)
    return returned_count >= effective_limit


def map_vercel_deployment_status(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the deployment count and how many failed, qualifying page-capped totals."""
    if not output.get("available"):
        return
    deployments = output.get("deployments") or []
    if not deployments:
        return
    total = output.get("total", len(deployments))
    truncated = _vercel_page_is_truncated(total, tool_input.get("limit", 10))
    total_label = f"{total}+" if truncated else str(total)
    failed_count = len(output.get("failed_deployments") or [])
    # A truncated page's failed-count is only a floor even when it's zero --
    # zero failures *in the returned page* does not mean zero overall.
    failed_label = f"{failed_count}+" if truncated else str(failed_count)
    record_evidence_entry(
        evidence,
        source="vercel_deployment_status",
        label="Vercel Deployment Status",
        summary=f"{total_label} deployment(s), {failed_label} failed",
    )


def map_vercel_deployment_logs(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the build event count, keyword-matched error count, and runtime log count."""
    if not output.get("available"):
        return
    total_events = output.get("total_events", 0)
    total_runtime_logs = output.get("total_runtime_logs", 0)
    if not total_events and not total_runtime_logs:
        return
    requested_limit = tool_input.get("limit", 100)
    parts = []
    if total_events:
        # A truncated page's error-keyword count is only a floor even when
        # it's zero -- zero matches in the returned page does not mean zero
        # overall.
        events_truncated = _vercel_logs_page_is_truncated(total_events, requested_limit)
        events_label = f"{total_events}+" if events_truncated else str(total_events)
        error_count = len(output.get("error_events") or [])
        error_label = f"{error_count}+" if events_truncated else str(error_count)
        parts.append(f"{events_label} event(s), {error_label} matching an error keyword")
    if total_runtime_logs:
        logs_truncated = _vercel_logs_page_is_truncated(total_runtime_logs, requested_limit)
        logs_label = f"{total_runtime_logs}+" if logs_truncated else str(total_runtime_logs)
        parts.append(f"{logs_label} runtime log(s)")
    record_evidence_entry(
        evidence,
        source="vercel_deployment_logs",
        label="Vercel Deployment Logs",
        summary=", ".join(parts),
    )

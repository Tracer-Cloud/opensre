"""Evidence mappers for argocd_application_diff and argocd_application_status."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry


def map_argocd_application_diff(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    """Cite detected GitOps drift as report evidence.

    Records nothing when no drift was found -- "0 diffs, no drift" is a clean
    result, not a finding worth spending the agent's context budget on.
    ``drift_detected`` is the authoritative signal, not ``diff_count``: the
    client's ``modified`` flag (Argo CD v3.3's {items, modified} response
    shape) can report drift with an empty itemized diff list -- a modified-
    but-unitemized result must still be cited, not silently dropped.
    """
    if not output.get("available") or not output.get("drift_detected"):
        return
    diff_count = output.get("diff_count") or 0
    if diff_count:
        summary = f"{diff_count} object diff(s) — live state has drifted from GitOps"
    else:
        summary = "live state has drifted from GitOps (no itemized diffs returned)"
    record_evidence_entry(
        evidence,
        source="argocd_application_diff",
        label="Argo CD Application Diff",
        summary=summary,
    )


def map_argocd_application_status(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    """Cite the application's sync/health status, or the application list count."""
    if not output.get("available"):
        return
    application = output.get("application")
    if isinstance(application, dict) and application.get("name"):
        sync_status = application.get("sync_status") or "unknown"
        health_status = application.get("health_status") or "unknown"
        record_evidence_entry(
            evidence,
            source="argocd_application_status",
            label="Argo CD Application Status",
            summary=f"{application['name']}: sync={sync_status}, health={health_status}",
        )
        return
    applications = output.get("applications")
    if isinstance(applications, list) and applications:
        record_evidence_entry(
            evidence,
            source="argocd_application_status",
            label="Argo CD Applications",
            summary=f"{len(applications)} application(s) listed",
        )

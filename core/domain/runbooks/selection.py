"""Deterministic matching of incident identity to runbook catalog entries."""

from __future__ import annotations

from core.domain.runbooks.contracts import (
    IncidentIdentity,
    RunbookCatalogEntry,
    RunbookSelection,
    RunbookSelectionReason,
)


def _match_rank(
    entry: RunbookCatalogEntry,
    incident: IncidentIdentity,
) -> tuple[int, int, int] | None:
    match = entry.match
    incident_labels = incident.labels_by_name

    if match.alertname and match.alertname != incident.alertname:
        return None
    if match.service and match.service != incident.service:
        return None
    if any(incident_labels.get(name) != value for name, value in match.labels):
        return None

    if match.alertname and match.labels:
        return (3, len(match.labels), int(bool(match.service)))
    if match.alertname:
        return (2, 0, int(bool(match.service)))
    if match.service:
        return (1, len(match.labels), 0)
    return None


def _selection_details(
    entry: RunbookCatalogEntry,
) -> tuple[RunbookSelectionReason, tuple[str, ...]]:
    match = entry.match
    if match.alertname and match.labels:
        matched_fields = ["alertname", *(f"label:{name}" for name, _value in match.labels)]
        if match.service:
            matched_fields.append("service")
        return "alertname_labels", tuple(matched_fields)
    if match.alertname:
        alert_fields: tuple[str, ...] = (
            ("alertname", "service") if match.service else ("alertname",)
        )
        return "alertname", alert_fields
    matched_fields = ["service", *(f"label:{name}" for name, _value in match.labels)]
    return "service", tuple(matched_fields)


def select_runbook(
    entries: tuple[RunbookCatalogEntry, ...],
    incident: IncidentIdentity,
) -> RunbookSelection:
    """Select the uniquely most-specific exact match for ``incident``."""
    ranked = [
        (rank, entry) for entry in entries if (rank := _match_rank(entry, incident)) is not None
    ]
    if not ranked:
        return RunbookSelection(status="not_found")

    best_rank = max(rank for rank, _entry in ranked)
    candidates = tuple(entry for rank, entry in ranked if rank == best_rank)
    if len(candidates) != 1:
        return RunbookSelection(
            status="ambiguous",
            candidate_ids=tuple(sorted(entry.document_id for entry in candidates)),
            specificity=best_rank,
        )

    entry = candidates[0]
    reason, fields = _selection_details(entry)
    return RunbookSelection(
        status="matched",
        entry=entry,
        reason=reason,
        matched_fields=fields,
        specificity=best_rank,
    )


__all__ = ["select_runbook"]

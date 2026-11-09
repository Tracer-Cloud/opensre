"""Pair raw ``NrAiIncident`` transition rows into one record per incident.

``NrAiIncident`` emits one row per state transition, not one row per
incident (spec.md Decision 2, confirmed against a real account): a resolved
incident produces two rows sharing the same ``incidentId`` — one
``event="open"`` (``closeTime=null``) and one ``event="close"``
(``closeTime`` populated). An incident still open only has the ``open`` row.

Pairing happens by ``incidentId``, never by ``conditionName`` + entity:
repeated firings of the same condition carry **distinct** ``incidentId``s and
are a real flapping signal that must not be collapsed.
"""

from __future__ import annotations

import html
from typing import Any

_EVENT_CLOSE = "close"

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"


def _decode_html_entities(value: Any) -> Any:
    """Decode ``&gt;=``-style HTML entities in ``operator``/``title`` values.

    The real account returns these fields HTML-entity-encoded (spec.md
    Decision 2); returning the encoded form would make the LLM read a
    literal ``&gt;=`` instead of ``>=``.
    """
    if isinstance(value, str):
        return html.unescape(value)
    return value


def parse_threshold(raw: Any) -> tuple[float | None, str | None]:
    """Parse ``threshold`` from its NerdGraph string form (e.g. ``"3.0"``).

    Returns ``(parsed, raw_string)``. ``threshold`` always arrives as a JSON
    string, never a number. When it can't be parsed as a float, ``parsed`` is
    ``None`` and ``raw_string`` preserves the original value as a fallback
    note instead of crashing the parser.
    """
    if raw is None:
        return None, None
    text = str(raw)
    try:
        return float(text), text
    except ValueError:
        return None, text


def _event_kind(row: dict[str, Any]) -> str:
    """Return ``"open"``/``"close"``/``""`` for *row*, compared case-insensitively.

    The official docs show ``event`` capitalized (``Open``/``Close``); the
    real account returned it lowercase. Neither source is trusted for casing.
    """
    return str(row.get("event") or "").strip().lower()


def _incident_id(row: dict[str, Any]) -> str | None:
    incident_id = row.get("incidentId")
    return None if incident_id is None else str(incident_id)


def parse_incident_rows(
    rows: list[dict[str, Any]],
    *,
    requested_limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Pair raw ``NrAiIncident`` rows into one normalized record per incident.

    Returns ``(incidents, truncated)``. Incidents are sorted by ``openTime``
    ascending to preserve causal order regardless of the order NerdGraph
    returned the raw rows in. ``truncated`` is set when the raw row count hit
    the requested cap — pairing can only shrink the count, never grow it, so
    a full page of raw rows means some transitions may be missing.
    """
    open_rows: dict[str, dict[str, Any]] = {}
    close_rows: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    seen: set[str] = set()

    for row in rows:
        if not isinstance(row, dict):
            continue
        incident_id = _incident_id(row)
        if incident_id is None:
            continue
        if incident_id not in seen:
            seen.add(incident_id)
            order.append(incident_id)
        if _event_kind(row) == _EVENT_CLOSE:
            close_rows[incident_id] = row
        else:
            # Anything that isn't a recognized "close" transition is the
            # opening row (covers "open" and any unexpected event value).
            open_rows.setdefault(incident_id, row)

    incidents: list[dict[str, Any]] = []
    for incident_id in order:
        open_row = open_rows.get(incident_id)
        close_row = close_rows.get(incident_id)
        # The close row carries the final title/context (Decision 2) — it
        # wins whenever both exist.
        canonical = close_row if close_row is not None else open_row
        if canonical is None:
            continue

        threshold, threshold_raw = parse_threshold(canonical.get("threshold"))
        incidents.append(
            {
                "incident_id": incident_id,
                "status": STATUS_CLOSED if close_row is not None else STATUS_OPEN,
                "priority": canonical.get("priority"),
                "title": _decode_html_entities(canonical.get("title")),
                "condition_name": canonical.get("conditionName"),
                "policy_name": canonical.get("policyName"),
                "nrql_query": canonical.get("nrqlQuery"),
                "threshold": threshold,
                "threshold_raw": threshold_raw,
                "operator": _decode_html_entities(canonical.get("operator")),
                "runbook_url": canonical.get("runbookUrl"),
                "entity_name": canonical.get("entity.name"),
                "entity_guid": canonical.get("entity.guid"),
                "muted": bool(canonical.get("muted", False)),
                "open_time": (open_row or canonical).get("openTime"),
                "close_time": close_row.get("closeTime") if close_row is not None else None,
            }
        )

    incidents.sort(key=lambda incident: incident["open_time"] or 0)
    truncated = len(rows) >= requested_limit
    return incidents, truncated

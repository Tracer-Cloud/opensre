"""Uptime digest rollup from the watch transition log for morning digests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.sentry.uptime import (
    UptimeTransitionRecord,
    WatchState,
    load_all_watch_states,
    parse_transition_at,
)


def _sentry_available(sources: dict[str, dict]) -> bool:
    # Local-disk rollup only — do not require live Sentry API connectivity.
    return "sentry" in sources


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    sentry = sources.get("sentry") or {}
    return {"project_slug": sentry.get("project_slug", "")}


def _empty_rollup(*, window_hours: int, uptime_watch_active: bool, message: str) -> dict[str, Any]:
    return {
        "source": "sentry",
        "available": True,
        "uptime_watch_active": uptime_watch_active,
        "window_hours": window_hours,
        "still_down_count": 0,
        "recovered_count": 0,
        "still_down": [],
        "recovered": [],
        "message": message,
    }


def _monitor_label(*, name: str, url: str) -> str:
    candidate = url.strip()
    if candidate:
        parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
        host = (parsed.hostname or parsed.path or "").strip()
        if host:
            return host
    return name.strip() or "unknown monitor"


def _merge_transitions(
    states: dict[str, WatchState],
    *,
    project_slug: str,
) -> tuple[list[UptimeTransitionRecord], set[str]]:
    scope = project_slug.strip()
    transitions: list[UptimeTransitionRecord] = []
    open_incidents: set[str] = set()
    seen: set[tuple[str, str, str]] = set()
    monitor_projects: dict[str, set[str]] = {}

    for state in states.values():
        for record in state.transitions:
            if record.project_slug:
                monitor_projects.setdefault(record.monitor_id, set()).add(record.project_slug)
            if scope and record.project_slug != scope:
                continue
            key = (record.monitor_id, record.kind, record.at)
            if key in seen:
                continue
            seen.add(key)
            transitions.append(record)
        for monitor_id in state.open_incidents:
            if scope:
                projects = monitor_projects.get(monitor_id, set())
                if not projects or scope not in projects:
                    continue
            open_incidents.add(str(monitor_id))

    transitions.sort(key=lambda r: r.at)
    return transitions, open_incidents


def _label_for_records(records: list[UptimeTransitionRecord]) -> str:
    for record in reversed(records):
        label = _monitor_label(name=record.name, url=record.url)
        if label != "unknown monitor":
            return label
    return "unknown monitor"


def _scan_timeline(
    records: list[UptimeTransitionRecord],
    *,
    window_start: datetime,
) -> tuple[datetime | None, datetime | None]:
    open_down_at: datetime | None = None
    recovered_at: datetime | None = None

    for record in records:
        parsed = parse_transition_at(record)
        if parsed is None:
            continue
        if record.kind == "down":
            open_down_at = parsed
        elif record.kind == "recovered":
            if parsed >= window_start:
                recovered_at = parsed
            open_down_at = None

    return open_down_at, recovered_at


def _build_rollup(
    transitions: list[UptimeTransitionRecord],
    open_incidents: set[str],
    *,
    window_hours: int,
    now: datetime,
) -> dict[str, Any]:
    """Build the structured uptime rollup payload for the agent."""
    window_start = now - timedelta(hours=max(window_hours, 1))

    grouped: dict[str, list[UptimeTransitionRecord]] = {}
    for record in transitions:
        grouped.setdefault(record.monitor_id, []).append(record)

    still_down: list[dict[str, str]] = []
    recovered: list[dict[str, str]] = []
    seen_labels: set[str] = set()

    for monitor_id in open_incidents:
        records = grouped.get(monitor_id, [])
        open_down_at, _ = _scan_timeline(records, window_start=window_start)
        label = _label_for_records(records) if records else "unknown monitor"
        label_key = label.lower()
        if label_key in seen_labels:
            continue
        seen_labels.add(label_key)

        if open_down_at is not None:
            since = open_down_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
            still_down.append({"monitor": label, "since": since})
        else:
            still_down.append({"monitor": label, "since": "start time unavailable"})

    for monitor_id, records in grouped.items():
        if monitor_id in open_incidents:
            continue
        _, rec_at = _scan_timeline(records, window_start=window_start)
        if rec_at is None:
            continue
        label = _label_for_records(records)
        label_key = label.lower()
        if label_key in seen_labels:
            continue
        seen_labels.add(label_key)
        stamp = rec_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        recovered.append({"monitor": label, "recovered_at": stamp})

    return {
        "window_hours": window_hours,
        "still_down_count": len(still_down),
        "recovered_count": len(recovered),
        "still_down": still_down,
        "recovered": recovered,
    }


@tool(
    name="get_sentry_uptime_digest",
    source="sentry",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description=(
        "Retrieve an uptime digest rollup from the local watch transition log. "
        "Returns monitors that are still down or recovered within the time window. "
        "Use for morning digests — complements search_sentry_issues with uptime context."
    ),
    use_cases=[
        "Building the uptime section of a morning digest",
        "Checking recent downtime/recovery events from the watch history",
        "Summarizing uptime incidents alongside Sentry issue data",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "window_hours": {
                "type": "integer",
                "default": 24,
                "description": "How many hours of history to consider (default 24).",
            },
            "project_slug": {
                "type": "string",
                "default": "",
                "description": "Optional Sentry project to scope the rollup.",
            },
        },
        "required": [],
    },
    injected_params=("project_slug",),
    is_available=_sentry_available,
    extract_params=_extract_params,
    surfaces=(ToolSurface.CHAT,),
)
def get_sentry_uptime_digest(
    window_hours: int = 24,
    project_slug: str = "",
) -> dict[str, Any]:
    """Return structured uptime rollup from the watch transition log."""
    hours = max(int(window_hours), 1)
    now = datetime.now(UTC)
    states = load_all_watch_states()
    if not states:
        return _empty_rollup(
            window_hours=hours,
            uptime_watch_active=False,
            message="No uptime watch history found. Schedule an uptime watch first.",
        )

    transitions, open_incidents = _merge_transitions(states, project_slug=project_slug)
    if not transitions and not open_incidents:
        return _empty_rollup(
            window_hours=hours,
            uptime_watch_active=True,
            message="No uptime incidents in the requested window.",
        )

    rollup = _build_rollup(transitions, open_incidents, window_hours=hours, now=now)
    result: dict[str, Any] = {
        "source": "sentry",
        "available": True,
        "uptime_watch_active": True,
        **rollup,
    }
    if not rollup["still_down"] and not rollup["recovered"]:
        result["message"] = "No uptime incidents in the requested window."
    return result

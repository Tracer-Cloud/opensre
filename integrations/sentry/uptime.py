"""Sentry uptime monitor polling, state transitions, and notify messages.

v1 for #4032: REST poll of organization uptime monitors + DOWN/RECOVERED
transition messages. Transition history is persisted for morning-digest rollup (#4070).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Any, Literal

import httpx

import integrations.sentry.client as _sentry_client
from config.constants import OPENSRE_HOME_DIR
from integrations.sentry import (
    SentryConfig,
    build_sentry_config,
    describe_sentry_api_error,
    sentry_config_from_env,
)
from integrations.store import get_integration

logger = logging.getLogger(__name__)

# Sentry UptimeStatus IntEnum: OK=1, FAILED=2
_UPTIME_STATUS_OK = 1
_UPTIME_STATUS_FAILED = 2

MonitorHealth = Literal["up", "down", "unknown"]
TransitionKind = Literal["down", "recovered"]

_TRANSITION_RETENTION_DAYS = 7

_ALERTS_READ_HINT = (
    "Uptime monitor listing requires a Sentry auth token with alerts:read "
    "(or org:read). The Issues-only event:read scope is not enough — update "
    "the token scopes and re-run `opensre integrations setup` / verify sentry."
)


@dataclass(frozen=True)
class UptimeMonitor:
    """Normalized Sentry uptime monitor row."""

    id: str
    name: str
    url: str
    project_slug: str
    health: MonitorHealth
    uptime_status: int | None
    status: str


@dataclass(frozen=True)
class UptimeTransition:
    """A health transition worth notifying about."""

    kind: TransitionKind
    monitor: UptimeMonitor


@dataclass(frozen=True)
class UptimeTransitionRecord:
    """Persisted transition event for morning-digest history."""

    monitor_id: str
    kind: TransitionKind
    at: str
    name: str
    url: str
    project_slug: str


@dataclass
class WatchState:
    """Persisted per-task watch snapshot."""

    health: dict[str, MonitorHealth] = field(default_factory=dict)
    open_incidents: set[str] = field(default_factory=set)
    transitions: list[UptimeTransitionRecord] = field(default_factory=list)


def _store_credentials(store: dict[str, Any]) -> dict[str, Any]:
    """Extract Sentry credentials from an integration store record.

    Store shape is ``{credentials: {...}}`` (optionally with ``instances``),
    not a top-level ``config`` dict.
    """
    creds = store.get("credentials")
    if isinstance(creds, dict) and creds:
        return creds
    instances = store.get("instances")
    if isinstance(instances, list):
        for instance in instances:
            if not isinstance(instance, dict):
                continue
            nested = instance.get("credentials")
            if isinstance(nested, dict) and nested:
                return nested
    # Legacy / test shapes
    nested_config = store.get("config")
    if isinstance(nested_config, dict) and nested_config:
        return nested_config
    return store


def resolve_sentry_config(*, project_slug: str = "") -> SentryConfig | None:
    """Resolve Sentry REST config from env, then the integration store."""
    env_config = sentry_config_from_env()
    store = get_integration("sentry") or {}
    store_config = _store_credentials(store if isinstance(store, dict) else {})

    organization_slug = (env_config.organization_slug if env_config else "") or str(
        store_config.get("organization_slug") or ""
    ).strip()
    auth_token = (env_config.auth_token if env_config else "") or str(
        store_config.get("auth_token") or store_config.get("sentry_token") or ""
    ).strip()
    if not organization_slug or not auth_token:
        return None

    slug = (
        project_slug.strip()
        or (env_config.project_slug if env_config else "")
        or str(store_config.get("project_slug") or "").strip()
    )
    return build_sentry_config(
        {
            "base_url": (
                (env_config.base_url if env_config else "")
                or str(
                    store_config.get("base_url")
                    or store_config.get("sentry_url")
                    or "https://sentry.io"
                ).strip()
                or "https://sentry.io"
            ),
            "organization_slug": organization_slug,
            "auth_token": auth_token,
            "project_slug": slug,
        }
    )


def _normalize_health(uptime_status: Any) -> MonitorHealth:
    try:
        value = int(uptime_status)
    except (TypeError, ValueError):
        return "unknown"
    if value == _UPTIME_STATUS_FAILED:
        return "down"
    if value == _UPTIME_STATUS_OK:
        return "up"
    return "unknown"


def normalize_uptime_monitor(raw: dict[str, Any]) -> UptimeMonitor | None:
    """Normalize one Sentry uptime detector payload."""
    monitor_id = str(raw.get("id") or "").strip()
    if not monitor_id:
        return None
    uptime_status = raw.get("uptimeStatus", raw.get("uptime_status"))
    return UptimeMonitor(
        id=monitor_id,
        name=str(raw.get("name") or "").strip() or f"uptime-{monitor_id}",
        url=str(raw.get("url") or "").strip(),
        project_slug=str(raw.get("projectSlug") or raw.get("project_slug") or "").strip(),
        health=_normalize_health(uptime_status),
        uptime_status=(
            int(uptime_status)
            if isinstance(uptime_status, int)
            or (isinstance(uptime_status, str) and uptime_status.isdigit())
            else None
        ),
        status=str(raw.get("status") or "").strip() or "unknown",
    )


def list_sentry_uptime_monitors(*, config: SentryConfig) -> list[UptimeMonitor]:
    """List organization uptime monitors via REST.

    Endpoint: ``GET /api/0/organizations/{org}/uptime/``
    """
    path = f"/api/0/organizations/{config.organization_slug}/uptime/"
    params: list[tuple[str, str | int | float | bool | None]] = []
    if config.project_slug:
        params.append(("project", config.project_slug))

    try:
        payload = _sentry_client._request_json(config, "GET", path, params=params or None)
    except httpx.HTTPStatusError as err:
        detail = describe_sentry_api_error(
            err,
            project_slug=config.project_slug,
        )
        if err.response.status_code == HTTPStatus.FORBIDDEN:
            detail = f"{detail} {_ALERTS_READ_HINT}"
        raise RuntimeError(detail) from err

    rows = payload if isinstance(payload, list) else []
    monitors: list[UptimeMonitor] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized = normalize_uptime_monitor(row)
        if normalized is not None:
            monitors.append(normalized)
    return monitors


def health_snapshot(
    monitors: list[UptimeMonitor],
    *,
    previous: dict[str, MonitorHealth] | None = None,
) -> dict[str, MonitorHealth]:
    """Map monitor id → health for persistence.

    Unknown samples do not clobber a known prior value so a transient
    ``down → unknown → up`` sequence still recovers cleanly.
    """
    prior = previous or {}
    out: dict[str, MonitorHealth] = {}
    for monitor in monitors:
        if monitor.health == "unknown" and prior.get(monitor.id) in ("up", "down"):
            out[monitor.id] = prior[monitor.id]
        else:
            out[monitor.id] = monitor.health
    return out


def detect_uptime_transitions(
    previous: dict[str, MonitorHealth],
    monitors: list[UptimeMonitor],
    *,
    open_incidents: set[str] | None = None,
    notify_initial_down: bool = True,
) -> tuple[list[UptimeTransition], set[str]]:
    """Return DOWN / RECOVERED transitions and the updated open-incident set.

    Recovery is keyed off *open_incidents*, not only the prior health value, so
    ``down → unknown → up`` still emits RECOVERED.
    """
    transitions: list[UptimeTransition] = []
    open_set = set(open_incidents or ())
    by_id = {monitor.id: monitor for monitor in monitors}

    if not previous and not open_set:
        if notify_initial_down:
            for monitor in monitors:
                if monitor.health == "down":
                    transitions.append(UptimeTransition(kind="down", monitor=monitor))
                    open_set.add(monitor.id)
        return transitions, open_set

    for monitor in by_id.values():
        if monitor.health == "down" and monitor.id not in open_set:
            transitions.append(UptimeTransition(kind="down", monitor=monitor))
            open_set.add(monitor.id)
        elif monitor.health == "up" and monitor.id in open_set:
            transitions.append(UptimeTransition(kind="recovered", monitor=monitor))
            open_set.discard(monitor.id)
        # unknown: leave open_incidents unchanged

    for monitor_id in list(open_set):
        if monitor_id in by_id:
            continue
        logger.info("Previously open uptime incident %s no longer listed", monitor_id)

    return transitions, open_set


def format_uptime_transition_message(transitions: list[UptimeTransition]) -> str:
    """Format a plain-text notify body for delivery providers."""
    if not transitions:
        return ""

    lines = ["Sentry uptime watch"]
    for transition in transitions:
        monitor = transition.monitor
        target = monitor.url or monitor.name
        project = f" [{monitor.project_slug}]" if monitor.project_slug else ""
        if transition.kind == "down":
            lines.append(f"CRITICAL downtime{project}: {monitor.name} — {target}")
        else:
            lines.append(f"RECOVERED{project}: {monitor.name} — {target}")
    return "\n".join(lines)


def format_uptime_watch_active_message(
    *,
    task_id: str,
    cron: str,
    timezone: str,
    project_slug: str = "",
) -> str:
    """One-shot confirmation when a watch schedule is created."""
    scope = (
        f"Sentry project `{project_slug}`" if project_slug.strip() else "all Sentry uptime monitors"
    )
    return (
        "Sentry uptime watch is **active**.\n"
        f"OpenSRE will poll {scope} on schedule `{cron}` ({timezone}) "
        "and ping this chat when a monitor goes **down** or **recovers**.\n"
        "Quiet polls (no change) send nothing.\n"
        f"Task id: `{task_id}`"
    )


def _state_path() -> Path:
    return OPENSRE_HOME_DIR / "sentry_uptime_watch_state.json"


def parse_transition_at(value: UptimeTransitionRecord | str) -> datetime | None:
    """Parse an ISO-8601 transition timestamp."""
    raw = value.at if isinstance(value, UptimeTransitionRecord) else value
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _load_state_store(*, path: Path | None = None) -> dict[str, Any]:
    """Read the raw uptime watch state mapping from disk."""
    store_path = path or _state_path()
    if not store_path.exists():
        return {}
    try:
        raw = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read uptime watch state: %s", exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def _transition_record_payload(record: UptimeTransitionRecord) -> dict[str, str]:
    return {
        "monitor_id": record.monitor_id,
        "kind": record.kind,
        "at": record.at,
        "name": record.name,
        "url": record.url,
        "project_slug": record.project_slug,
    }


def _parse_transition_record(raw: object) -> UptimeTransitionRecord | None:
    if not isinstance(raw, dict):
        return None
    monitor_id = str(raw.get("monitor_id") or "").strip()
    kind = raw.get("kind")
    at = str(raw.get("at") or "").strip()
    if not monitor_id or kind not in ("down", "recovered") or not at:
        return None
    return UptimeTransitionRecord(
        monitor_id=monitor_id,
        kind=kind,  # type: ignore[arg-type]
        at=at,
        name=str(raw.get("name") or "").strip(),
        url=str(raw.get("url") or "").strip(),
        project_slug=str(raw.get("project_slug") or "").strip(),
    )


def _parse_watch_state_entry(entry: dict[str, Any]) -> WatchState:
    health: dict[str, MonitorHealth] = {}
    snapshot = entry.get("health")
    if isinstance(snapshot, dict):
        for key, value in snapshot.items():
            if value in ("up", "down", "unknown"):
                health[str(key)] = value  # type: ignore[assignment]

    open_incidents: set[str] = set()
    incidents = entry.get("open_incidents")
    if isinstance(incidents, list):
        open_incidents = {str(item) for item in incidents if str(item).strip()}
    else:
        open_incidents = {mid for mid, status in health.items() if status == "down"}

    transitions: list[UptimeTransitionRecord] = []
    raw_transitions = entry.get("transitions")
    if isinstance(raw_transitions, list):
        for item in raw_transitions:
            record = _parse_transition_record(item)
            if record is not None:
                transitions.append(record)

    return WatchState(
        health=health,
        open_incidents=open_incidents,
        transitions=transitions,
    )


def transition_record_from(
    transition: UptimeTransition,
    *,
    at: datetime | None = None,
) -> UptimeTransitionRecord:
    """Build a persisted record from a live transition."""
    monitor = transition.monitor
    timestamp = (at or datetime.now(UTC)).isoformat()
    return UptimeTransitionRecord(
        monitor_id=monitor.id,
        kind=transition.kind,
        at=timestamp,
        name=monitor.name,
        url=monitor.url,
        project_slug=monitor.project_slug,
    )


def append_transition_records(
    state: WatchState,
    transitions: list[UptimeTransition],
    *,
    at: datetime | None = None,
) -> None:
    """Append new transition records and prune entries older than retention."""
    now = at or datetime.now(UTC)
    for transition in transitions:
        state.transitions.append(transition_record_from(transition, at=now))
    state.transitions = prune_transition_records(
        state.transitions,
        now=now,
        open_incident_ids=state.open_incidents,
    )


def prune_transition_records(
    records: list[UptimeTransitionRecord],
    *,
    now: datetime | None = None,
    retention_days: int = _TRANSITION_RETENTION_DAYS,
    open_incident_ids: set[str] | None = None,
) -> list[UptimeTransitionRecord]:
    """Drop aged transitions; retain founding DOWN rows for open incidents."""
    current = now or datetime.now(UTC)
    cutoff = current - timedelta(days=retention_days)
    pinned_monitor_ids = {str(item) for item in (open_incident_ids or set()) if str(item).strip()}

    pinned_down: dict[str, UptimeTransitionRecord] = {}
    for record in records:
        if record.monitor_id not in pinned_monitor_ids or record.kind != "down":
            continue
        parsed = parse_transition_at(record)
        if parsed is None:
            continue
        existing = pinned_down.get(record.monitor_id)
        if existing is None:
            pinned_down[record.monitor_id] = record
            continue
        existing_at = parse_transition_at(existing)
        if existing_at is None or parsed < existing_at:
            pinned_down[record.monitor_id] = record

    pinned_keys = {(record.monitor_id, record.kind, record.at) for record in pinned_down.values()}
    kept: list[UptimeTransitionRecord] = []
    seen_pinned: set[tuple[str, str, str]] = set()
    for record in records:
        key = (record.monitor_id, record.kind, record.at)
        if key in pinned_keys:
            if key not in seen_pinned:
                kept.append(record)
                seen_pinned.add(key)
            continue
        parsed = parse_transition_at(record)
        if parsed is not None and parsed >= cutoff:
            kept.append(record)
    return kept


def load_all_watch_states(*, path: Path | None = None) -> dict[str, WatchState]:
    """Load every task entry from the uptime watch state file."""
    return {
        str(task_id): _parse_watch_state_entry(entry)
        for task_id, entry in _load_state_store(path=path).items()
        if isinstance(entry, dict)
    }


def load_watch_state(
    task_id: str,
    *,
    path: Path | None = None,
) -> WatchState:
    """Load prior health snapshot and open incidents for a watch task."""
    entry = _load_state_store(path=path).get(task_id)
    if not isinstance(entry, dict):
        return WatchState()
    return _parse_watch_state_entry(entry)


def save_watch_state(
    task_id: str,
    state: WatchState,
    *,
    path: Path | None = None,
) -> None:
    """Persist watch state atomically (temp file + replace)."""
    store_path = path or _state_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if store_path.exists():
        try:
            loaded = json.loads(store_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, json.JSONDecodeError):
            existing = {}
    existing[task_id] = {
        "health": state.health,
        "open_incidents": sorted(state.open_incidents),
        "transitions": [_transition_record_payload(record) for record in state.transitions],
    }
    payload = json.dumps(existing, indent=2, sort_keys=True) + "\n"
    tmp_path = store_path.with_suffix(store_path.suffix + f".{os.getpid()}.tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    tmp_path.replace(store_path)


def run_uptime_watch_tick(
    *,
    task_id: str,
    project_slug: str = "",
    state_path: Path | None = None,
    notify_initial_down: bool = True,
) -> str:
    """Poll uptime monitors and return a notify message (empty if quiet)."""
    config = resolve_sentry_config(project_slug=project_slug)
    if config is None:
        raise RuntimeError(
            "Sentry is not configured. Run `opensre integrations setup` and verify "
            "with `opensre integrations verify sentry`."
        )

    monitors = list_sentry_uptime_monitors(config=config)
    previous = load_watch_state(task_id, path=state_path)
    transitions, open_incidents = detect_uptime_transitions(
        previous.health,
        monitors,
        open_incidents=previous.open_incidents,
        notify_initial_down=notify_initial_down,
    )
    next_state = WatchState(
        health=health_snapshot(monitors, previous=previous.health),
        open_incidents=open_incidents,
        transitions=list(previous.transitions),
    )
    append_transition_records(next_state, transitions)
    save_watch_state(task_id, next_state, path=state_path)
    return format_uptime_transition_message(transitions)


__all__ = [
    "UptimeMonitor",
    "UptimeTransition",
    "UptimeTransitionRecord",
    "WatchState",
    "append_transition_records",
    "detect_uptime_transitions",
    "format_uptime_transition_message",
    "format_uptime_watch_active_message",
    "health_snapshot",
    "list_sentry_uptime_monitors",
    "load_all_watch_states",
    "load_watch_state",
    "normalize_uptime_monitor",
    "parse_transition_at",
    "prune_transition_records",
    "resolve_sentry_config",
    "run_uptime_watch_tick",
    "save_watch_state",
    "transition_record_from",
]

"""Unit tests for Sentry uptime watch helpers (#4032)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from integrations.sentry import SentryConfig
from integrations.sentry.uptime import (
    UptimeMonitor,
    UptimeTransitionRecord,
    WatchState,
    append_transition_records,
    detect_uptime_transitions,
    format_uptime_transition_message,
    health_snapshot,
    list_sentry_uptime_monitors,
    load_all_watch_states,
    load_watch_state,
    normalize_uptime_monitor,
    prune_transition_records,
    run_uptime_watch_tick,
    save_watch_state,
    transition_record_from,
)


def _monitor(
    monitor_id: str,
    *,
    health: str = "up",
    url: str = "https://example.com",
    name: str = "example",
) -> UptimeMonitor:
    status = 2 if health == "down" else 1 if health == "up" else None
    return UptimeMonitor(
        id=monitor_id,
        name=name,
        url=url,
        project_slug="tracer-30",
        health=health,  # type: ignore[arg-type]
        uptime_status=status,
        status="active",
    )


def test_normalize_uptime_monitor_maps_failed_status() -> None:
    monitor = normalize_uptime_monitor(
        {
            "id": "99",
            "name": "docs",
            "url": "https://docs.example.com",
            "projectSlug": "web",
            "uptimeStatus": 2,
            "status": "active",
        }
    )
    assert monitor is not None
    assert monitor.health == "down"
    assert monitor.project_slug == "web"


def test_detect_transitions_initial_down_and_recovery() -> None:
    down = _monitor("1", health="down")
    up = _monitor("1", health="up")

    initial, open_set = detect_uptime_transitions({}, [down], notify_initial_down=True)
    assert len(initial) == 1
    assert initial[0].kind == "down"
    assert open_set == {"1"}

    recovered, open_after = detect_uptime_transitions(
        {"1": "down"},
        [up],
        open_incidents={"1"},
    )
    assert len(recovered) == 1
    assert recovered[0].kind == "recovered"
    assert open_after == set()

    quiet, open_quiet = detect_uptime_transitions({"1": "up"}, [up], open_incidents=set())
    assert quiet == []
    assert open_quiet == set()


def test_detect_transitions_recovers_after_unknown() -> None:
    """down → unknown → up must still emit RECOVERED (Greptile P1)."""
    unknown = _monitor("1", health="unknown")
    up = _monitor("1", health="up")

    transitions, open_set = detect_uptime_transitions(
        {"1": "down"},
        [unknown],
        open_incidents={"1"},
    )
    assert transitions == []
    assert open_set == {"1"}

    recovered, open_after = detect_uptime_transitions(
        {"1": "down"},
        [up],
        open_incidents=open_set,
    )
    assert len(recovered) == 1
    assert recovered[0].kind == "recovered"
    assert open_after == set()


def test_health_snapshot_preserves_known_over_unknown() -> None:
    unknown = _monitor("1", health="unknown")
    assert health_snapshot([unknown], previous={"1": "down"}) == {"1": "down"}


def test_format_uptime_watch_active_message() -> None:
    from integrations.sentry.uptime import format_uptime_watch_active_message

    message = format_uptime_watch_active_message(
        task_id="abc123",
        cron="*/5 * * * *",
        timezone="UTC",
        project_slug="marketing-website",
    )
    assert "active" in message.lower()
    assert "down" in message.lower()
    assert "abc123" in message
    assert "marketing-website" in message


def test_format_message_marks_critical_downtime() -> None:
    transitions, _ = detect_uptime_transitions({}, [_monitor("1", health="down", name="api")])
    message = format_uptime_transition_message(transitions)
    assert "CRITICAL downtime" in message
    assert "api" in message
    assert format_uptime_transition_message([]) == ""


def test_watch_state_roundtrip_atomic(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_watch_state(
        "task-a",
        WatchState(health={"1": "down", "2": "up"}, open_incidents={"1"}),
        path=path,
    )
    loaded = load_watch_state("task-a", path=path)
    assert loaded.health == {"1": "down", "2": "up"}
    assert loaded.open_incidents == {"1"}
    assert load_watch_state("missing", path=path).health == {}
    assert not list(tmp_path.glob("*.tmp"))


def test_list_sentry_uptime_monitors_parses_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_request(
        config: SentryConfig,
        method: str,
        path: str,
        *,
        params: list | None = None,
    ) -> list[dict]:
        assert method == "GET"
        assert path.endswith("/uptime/")
        assert ("project", "web") in (params or [])
        return [
            {
                "id": "7",
                "name": "homepage",
                "url": "https://example.com",
                "projectSlug": "web",
                "uptimeStatus": 1,
                "status": "active",
            }
        ]

    monkeypatch.setattr("integrations.sentry.client._request_json", _fake_request)
    config = SentryConfig(
        organization_slug="acme",
        auth_token="token",
        project_slug="web",
    )
    monitors = list_sentry_uptime_monitors(config=config)
    assert len(monitors) == 1
    assert monitors[0].health == "up"
    assert health_snapshot(monitors) == {"7": "up"}


def test_list_sentry_uptime_monitors_403_includes_alerts_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("GET", "https://sentry.io/api/0/organizations/acme/uptime/")
    response = httpx.Response(403, request=request, text='{"detail":"forbidden"}')

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise httpx.HTTPStatusError("forbidden", request=request, response=response)

    monkeypatch.setattr("integrations.sentry.client._request_json", _raise)
    config = SentryConfig(organization_slug="acme", auth_token="token")
    with pytest.raises(RuntimeError, match="alerts:read"):
        list_sentry_uptime_monitors(config=config)


def test_resolve_sentry_config_reads_store_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.sentry.uptime.sentry_config_from_env", lambda: None)
    monkeypatch.setattr(
        "integrations.sentry.uptime.get_integration",
        lambda _service: {
            "service": "sentry",
            "credentials": {
                "base_url": "https://sentry.io",
                "organization_slug": "tracer-30",
                "auth_token": "sntrys_test",
                "project_slug": "python",
            },
        },
    )
    from integrations.sentry.uptime import resolve_sentry_config

    config = resolve_sentry_config()
    assert config is not None
    assert config.organization_slug == "tracer-30"
    assert config.auth_token == "sntrys_test"
    assert config.project_slug == "python"


def test_run_uptime_watch_tick_notifies_then_quiets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    config = SentryConfig(organization_slug="acme", auth_token="token")
    monkeypatch.setattr(
        "integrations.sentry.uptime.resolve_sentry_config",
        lambda **_kwargs: config,
    )
    monkeypatch.setattr(
        "integrations.sentry.uptime.list_sentry_uptime_monitors",
        lambda **_kwargs: [_monitor("1", health="down", name="api")],
    )

    first = run_uptime_watch_tick(task_id="t1", state_path=state_path)
    assert "CRITICAL downtime" in first
    second = run_uptime_watch_tick(task_id="t1", state_path=state_path)
    assert second == ""


def test_transition_records_append_and_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    down = _monitor("1", health="down", url="https://sandbox.example.com", name="sandbox")
    transitions, open_set = detect_uptime_transitions({}, [down], notify_initial_down=True)
    state = WatchState(open_incidents=open_set)
    append_transition_records(state, transitions)
    save_watch_state("task-a", state, path=path)

    loaded = load_watch_state("task-a", path=path)
    assert len(loaded.transitions) == 1
    assert loaded.transitions[0].kind == "down"
    assert loaded.transitions[0].monitor_id == "1"
    assert loaded.transitions[0].url == "https://sandbox.example.com"

    all_states = load_all_watch_states(path=path)
    assert "task-a" in all_states
    assert len(all_states["task-a"].transitions) == 1


def test_prune_transition_records_drops_old_entries() -> None:
    from datetime import UTC, datetime, timedelta

    old = UptimeTransitionRecord(
        monitor_id="1",
        kind="down",
        at=(datetime.now(UTC) - timedelta(days=10)).isoformat(),
        name="old",
        url="https://old.example.com",
        project_slug="web",
    )
    recent = UptimeTransitionRecord(
        monitor_id="2",
        kind="down",
        at=datetime.now(UTC).isoformat(),
        name="recent",
        url="https://recent.example.com",
        project_slug="web",
    )
    pruned = prune_transition_records([old, recent], now=datetime.now(UTC))
    assert pruned == [recent]


def test_prune_transition_records_keeps_open_incident_down() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    old = UptimeTransitionRecord(
        monitor_id="1",
        kind="down",
        at=(now - timedelta(days=10)).isoformat(),
        name="sandbox",
        url="https://sandbox.example.com",
        project_slug="web",
    )
    pruned = prune_transition_records([old], now=now, open_incident_ids={"1"})
    assert pruned == [old]


def test_transition_record_from_uses_monitor_metadata() -> None:
    down = _monitor("9", health="down", name="api", url="https://api.example.com")
    transitions, _ = detect_uptime_transitions({}, [down], notify_initial_down=True)
    record = transition_record_from(transitions[0])
    assert record.monitor_id == "9"
    assert record.name == "api"
    assert record.project_slug == "tracer-30"

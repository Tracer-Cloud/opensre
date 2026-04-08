from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest

from app.remote.vercel_poller import (
    VercelPoller,
    VercelPollerSettings,
    VercelResolutionError,
    _merge_alerts,
    _sort_deployment_stubs_newest_first,
    collect_vercel_candidates,
    enrich_remote_alert_from_vercel,
    parse_vercel_url,
)
from app.services.vercel import VercelConfig


class _FakeVercelClient:
    def __init__(
        self,
        *,
        projects: list[dict[str, Any]],
        deployments: list[dict[str, Any]],
        deployment_details: dict[str, Any],
        events: list[dict[str, Any]],
        runtime_logs: list[dict[str, Any]],
    ) -> None:
        self._projects = projects
        self._deployments = deployments
        self._deployment_details = deployment_details
        self._events = events
        self._runtime_logs = runtime_logs

    def __enter__(self) -> _FakeVercelClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def list_projects(self, limit: int = 100) -> dict[str, Any]:
        return {"success": True, "projects": self._projects[:limit], "total": len(self._projects)}

    def list_deployments(
        self,
        project_id: str = "",
        limit: int = 10,
        state: str = "",
    ) -> dict[str, Any]:
        _ = state
        deployments = [
            deployment
            for deployment in self._deployments
            if not project_id or deployment.get("project_id") == project_id
        ]
        return {"success": True, "deployments": deployments[:limit], "total": len(deployments)}

    def get_deployment(self, deployment_id: str) -> dict[str, Any]:
        if deployment_id != self._deployment_details.get("id"):
            return {"success": False, "error": "not found"}
        return {"success": True, "deployment": self._deployment_details}

    def get_deployment_events(self, deployment_id: str, limit: int = 100) -> dict[str, Any]:
        if deployment_id != self._deployment_details.get("id"):
            return {"success": False, "error": "not found"}
        return {"success": True, "events": self._events[:limit], "total": len(self._events)}

    def get_runtime_logs(
        self,
        deployment_id: str,
        limit: int = 100,
        *,
        project_id: str = "",
    ) -> dict[str, Any]:
        _ = project_id
        if deployment_id != self._deployment_details.get("id"):
            return {"success": False, "error": "not found"}
        return {
            "success": True,
            "logs": self._runtime_logs[:limit],
            "total": len(self._runtime_logs),
        }


def _fake_client(selected_log_id: str = "log_selected") -> _FakeVercelClient:
    return _FakeVercelClient(
        projects=[{"id": "proj_123", "name": "tracer-marketing-website-v3"}],
        deployments=[
            {
                "id": "dpl_123",
                "project_id": "proj_123",
                "name": "tracer-marketing-website-v3",
                "state": "ERROR",
                "error": "Build failed",
                "meta": {
                    "github_repo": "org/tracer-marketing-website-v3",
                    "github_commit_sha": "abc123",
                    "github_commit_ref": "main",
                },
            }
        ],
        deployment_details={
            "id": "dpl_123",
            "name": "tracer-marketing-website-v3",
            "state": "ERROR",
            "error": "Build failed",
            "meta": {
                "github_repo": "org/tracer-marketing-website-v3",
                "github_commit_sha": "abc123",
                "github_commit_ref": "main",
            },
        },
        events=[{"id": "evt_1", "text": "Build failed: cannot resolve import"}],
        runtime_logs=[
            {
                "id": selected_log_id,
                "type": "stderr",
                "source": "lambda",
                "message": "Error: cannot resolve import",
                "payload": {"text": "Error: cannot resolve import"},
            }
        ],
    )


def test_sort_deployment_stubs_newest_first_orders_by_created_at() -> None:
    stubs = [
        {"id": "a", "created_at": "2026-04-01T00:00:00Z"},
        {"id": "b", "created_at": "2026-04-07T00:00:00Z"},
        {"id": "c", "created_at": "2026-04-03T00:00:00Z"},
        {"id": "", "created_at": "2099-01-01T00:00:00Z"},
    ]
    ordered = _sort_deployment_stubs_newest_first(stubs)
    assert [s["id"] for s in ordered] == ["b", "c", "a"]


def test_merge_alerts_preserves_explicit_false_from_original() -> None:
    canonical = {"is_noise": True, "alert_name": "from-vercel"}
    original = {"is_noise": False}
    merged = _merge_alerts(canonical=canonical, original=original)
    assert merged["is_noise"] is False


def test_merge_alerts_backfills_empty_string_from_canonical() -> None:
    canonical = {"text": "filled from canonical"}
    original = {"text": ""}
    merged = _merge_alerts(canonical=canonical, original=original)
    assert merged["text"] == "filled from canonical"


def test_parse_vercel_url_extracts_project_and_selected_log_id() -> None:
    parsed = parse_vercel_url(
        "https://vercel.com/vincenthus-projects/tracer-marketing-website-v3/logs"
        "?page=3&selectedLogId=54w4s-1775494460431-b04b1df81301&panelState=opened"
    )
    assert parsed.team_slug == "vincenthus-projects"
    assert parsed.project_slug == "tracer-marketing-website-v3"
    assert parsed.selected_log_id == "54w4s-1775494460431-b04b1df81301"


def test_enrich_remote_alert_from_vercel_resolves_selected_log_id(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.remote.vercel_poller.resolve_vercel_config",
        lambda: VercelConfig(api_token="tok_test", team_id=""),
    )
    monkeypatch.setattr(
        "app.remote.vercel_poller._make_client_from_config",
        lambda _config: _fake_client("54w4s-1775494460431-b04b1df81301"),
    )

    enriched = enrich_remote_alert_from_vercel({
        "vercel_url": (
            "https://vercel.com/vincenthus-projects/tracer-marketing-website-v3/logs"
            "?page=3&selectedLogId=54w4s-1775494460431-b04b1df81301&panelState=opened"
        )
    })

    assert enriched["alert_source"] == "vercel"
    assert enriched["vercel_deployment_id"] == "dpl_123"
    assert enriched["github_owner"] == "org"
    assert enriched["github_repo"] == "tracer-marketing-website-v3"
    assert enriched["sha"] == "abc123"
    assert enriched["branch"] == "main"
    assert "selectedLogId=54w4s-1775494460431-b04b1df81301" in enriched["annotations"]["log_excerpt"]


def test_collect_candidates_skips_processed_signatures(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "app.remote.vercel_poller.resolve_vercel_config",
        lambda: VercelConfig(api_token="tok_test", team_id=""),
    )
    monkeypatch.setattr(
        "app.remote.vercel_poller._make_client_from_config",
        lambda _config: _fake_client(),
    )
    settings = VercelPollerSettings(
        enabled=True,
        interval_seconds=300,
        project_allowlist=("proj_123",),
        deployment_limit=5,
        log_limit=20,
    )
    poller = VercelPoller(investigations_dir=tmp_path, settings=settings)

    candidates = poller.collect_candidates()
    assert len(candidates) == 1
    assert candidates[0].raw_alert["repository"] == "org/tracer-marketing-website-v3"

    poller.state_store.mark_processed(candidates[0].dedupe_key, candidates[0].signature)
    assert poller.collect_candidates() == []


def test_collect_vercel_candidates_returns_actionable_deployments(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.remote.vercel_poller.resolve_vercel_config",
        lambda: VercelConfig(api_token="tok_test", team_id=""),
    )
    monkeypatch.setattr(
        "app.remote.vercel_poller._make_client_from_config",
        lambda _config: _fake_client(),
    )

    candidates = collect_vercel_candidates()

    assert len(candidates) == 1
    assert candidates[0].raw_alert["vercel_deployment_state"] == "ERROR"
    assert candidates[0].raw_alert["error_message"] == "Build failed"


def test_collect_vercel_candidates_can_skip_runtime_logs(monkeypatch) -> None:
    runtime_calls = {"n": 0}
    fake = _fake_client()
    original_get_runtime_logs = fake.get_runtime_logs

    def _counting_get_runtime_logs(
        self: _FakeVercelClient,
        deployment_id: str,
        limit: int = 100,
        *,
        project_id: str = "",
    ) -> dict[str, Any]:
        runtime_calls["n"] += 1
        return original_get_runtime_logs(deployment_id, limit, project_id=project_id)

    fake.get_runtime_logs = types.MethodType(_counting_get_runtime_logs, fake)  # type: ignore[method-assign]

    monkeypatch.setattr(
        "app.remote.vercel_poller.resolve_vercel_config",
        lambda: VercelConfig(api_token="tok_test", team_id=""),
    )
    monkeypatch.setattr(
        "app.remote.vercel_poller._make_client_from_config",
        lambda _config: fake,
    )

    candidates = collect_vercel_candidates(include_runtime_logs=False)

    assert len(candidates) == 1
    assert runtime_calls["n"] == 0


def test_collect_vercel_candidates_treats_runtime_error_level_as_actionable(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.remote.vercel_poller.resolve_vercel_config",
        lambda: VercelConfig(api_token="tok_test", team_id=""),
    )
    monkeypatch.setattr(
        "app.remote.vercel_poller._make_client_from_config",
        lambda _config: _FakeVercelClient(
            projects=[{"id": "proj_123", "name": "tracer-marketing-website-v3"}],
            deployments=[
                {
                    "id": "dpl_123",
                    "project_id": "proj_123",
                    "name": "tracer-marketing-website-v3",
                    "state": "READY",
                    "error": "",
                    "meta": {},
                }
            ],
            deployment_details={
                "id": "dpl_123",
                "name": "tracer-marketing-website-v3",
                "state": "READY",
                "error": "",
                "meta": {},
            },
            events=[],
            runtime_logs=[
                {
                    "id": "log_error",
                    "level": "error",
                    "source": "request",
                    "message": "Request completed",
                    "status_code": 404,
                    "request_path": "/app-includes/css/buttons.css",
                }
            ],
        ),
    )

    candidates = collect_vercel_candidates()

    assert len(candidates) == 1
    assert candidates[0].raw_alert["vercel_deployment_state"] == "READY"


def test_collect_vercel_candidates_raises_on_api_error_when_requested(monkeypatch) -> None:
    class _FakeErrorVercelClient:
        def __enter__(self) -> _FakeErrorVercelClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def list_projects(self, limit: int = 100) -> dict[str, Any]:
            _ = limit
            return {"success": False, "error": "HTTP 403: invalidToken"}

    monkeypatch.setattr(
        "app.remote.vercel_poller.resolve_vercel_config",
        lambda: VercelConfig(api_token="tok_test", team_id=""),
    )
    monkeypatch.setattr(
        "app.remote.vercel_poller._make_client_from_config",
        lambda _config: _FakeErrorVercelClient(),
    )

    with pytest.raises(VercelResolutionError, match="Failed to list Vercel projects"):
        collect_vercel_candidates(fail_on_error=True)

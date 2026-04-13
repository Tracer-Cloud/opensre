from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import app.integrations.vercel_incidents as vercel_incidents
from app.remote.vercel_poller import VercelInvestigationCandidate, VercelResolutionError
from app.services.vercel import VercelConfig


class _Prompt:
    def __init__(self, answers: list[object]) -> None:
        self._answers = answers

    def ask(self) -> object:
        if not self._answers:
            return None
        return self._answers.pop(0)


def _project(
    *,
    project_id: str = "proj_123",
    name: str = "tracer-web",
    framework: str = "nextjs",
) -> dict[str, Any]:
    return {
        "id": project_id,
        "name": name,
        "framework": framework,
        "updated_at": "2026-04-06T00:00:00Z",
    }


def _candidate(*, deployment_id: str = "dpl_123", created_at: str = "200") -> VercelInvestigationCandidate:
    return VercelInvestigationCandidate(
        dedupe_key=deployment_id,
        signature=f"sig-{deployment_id}",
        raw_alert={
            "alert_name": "Vercel deployment issue: tracer-web",
            "pipeline_name": "tracer-web",
            "severity": "critical",
            "error_message": "Build failed: module import mismatch",
            "vercel_project_name": "tracer-web",
            "vercel_deployment_id": deployment_id,
            "vercel_deployment_state": "ERROR",
            "vercel_deployment_created_at": created_at,
            "vercel_url": "https://vercel.com/team/tracer-web/logs?selectedLogId=abc",
        },
        alert_name="Vercel deployment issue: tracer-web",
        pipeline_name="tracer-web",
        severity="critical",
    )


def test_cmd_vercel_incidents_json_outputs_incidents(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "app.integrations.vercel_incidents.resolve_vercel_config",
        lambda: VercelConfig(api_token="tok_test", team_id=""),
    )
    monkeypatch.setattr(
        "app.integrations.vercel_incidents.collect_vercel_candidates",
        lambda **_kwargs: [_candidate()],
    )
    monkeypatch.setattr("app.cli.context._root_obj", lambda: {"json": True})

    vercel_incidents.cmd_vercel_incidents(limit=5)

    captured = capsys.readouterr()
    assert '"deployment_id": "dpl_123"' in captured.out
    assert '"state": "ERROR"' in captured.out


def test_cmd_vercel_incidents_exits_on_api_error(monkeypatch, capsys) -> None:
    answers: list[object] = ["proj_123"]
    monkeypatch.setattr(
        vercel_incidents,
        "_load_projects",
        lambda: [_project()],
    )
    monkeypatch.setattr(
        "app.integrations.vercel_incidents.collect_vercel_candidates",
        lambda **_kwargs: (_ for _ in ()).throw(
            VercelResolutionError("Failed to list Vercel projects: HTTP 403: invalidToken")
        ),
    )
    monkeypatch.setattr("app.cli.context._root_obj", lambda: {"json": False})
    monkeypatch.setattr(
        vercel_incidents.questionary,
        "select",
        lambda *_args, **_kwargs: _Prompt(answers),
    )

    with pytest.raises(SystemExit) as exc_info:
        vercel_incidents.cmd_vercel_incidents(limit=5)

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "Failed to list Vercel projects" in captured.err
    assert "opensre integrations verify vercel" in captured.err
    assert "opensre integrations setup vercel" in captured.err


def test_select_incident_returns_selected_candidate(monkeypatch) -> None:
    answers: list[object] = ["dpl_123"]
    candidate = _candidate()
    monkeypatch.setattr(
        vercel_incidents.questionary,
        "select",
        lambda *_args, **_kwargs: _Prompt(answers),
    )

    result = vercel_incidents._select_incident([candidate])

    assert result == candidate


def test_project_label_formats_epoch_milliseconds() -> None:
    label = vercel_incidents._project_label({
        "id": "proj_123",
        "name": "tracer-marketing-website-v3",
        "framework": "nextjs",
        "updated_at": "1774890235837",
    })

    assert "1774890235837" not in label
    assert "updated " in label


def test_cmd_vercel_incidents_scopes_to_selected_project(monkeypatch) -> None:
    answers: list[object] = ["proj_123", "_exit"]
    captured: dict[str, Any] = {}
    monkeypatch.setattr("app.cli.context._root_obj", lambda: {"json": False})
    monkeypatch.setattr(
        vercel_incidents,
        "_load_projects",
        lambda: [_project(project_id="proj_123", name="tracer-web"), _project(project_id="proj_999", name="other-web")],
    )

    def _fake_collect(**kwargs: Any) -> list[VercelInvestigationCandidate]:
        captured["project_allowlist"] = kwargs.get("project_allowlist")
        return [_candidate()]

    monkeypatch.setattr(
        "app.integrations.vercel_incidents.collect_vercel_candidates",
        _fake_collect,
    )
    monkeypatch.setattr(
        vercel_incidents.questionary,
        "select",
        lambda *_args, **_kwargs: _Prompt(answers),
    )

    vercel_incidents.cmd_vercel_incidents(limit=5)

    assert captured["project_allowlist"] == ("proj_123", "tracer-web")


def test_incident_actions_can_execute_and_view_saved_rca(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    answers: list[object] = ["view", "run", "view", "back"]
    candidate = _candidate()
    monkeypatch.setattr(vercel_incidents, "_INCIDENT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        vercel_incidents.questionary,
        "select",
        lambda *_args, **_kwargs: _Prompt(answers),
    )
    monkeypatch.setattr(
        vercel_incidents,
        "run_investigation_cli_streaming",
        lambda **_kwargs: {
            "root_cause": "A broken import path shipped in the deployment.",
            "report": "The deployment failed during the build step.",
            "problem_md": "Build failed: module import mismatch",
            "is_noise": False,
        },
    )

    result = vercel_incidents._incident_actions(candidate)

    saved_report = tmp_path / "dpl_123.md"
    captured = capsys.readouterr()
    assert result == "back"
    assert saved_report.exists()
    assert "broken import path" in saved_report.read_text(encoding="utf-8")
    assert "No saved RCA for this incident yet" in captured.out
    assert "Saved RCA report ->" in captured.out

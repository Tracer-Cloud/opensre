"""Interactive CLI helpers for browsing Vercel incidents and RCA reports."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import questionary

from app.cli.context import is_json_output
from app.cli.investigate import run_investigation_cli, run_investigation_cli_streaming
from app.integrations.store import STORE_PATH
from app.remote.vercel_poller import (
    VercelInvestigationCandidate,
    VercelResolutionError,
    collect_vercel_candidates,
    resolve_vercel_config,
)
from app.services.vercel import make_vercel_client

_INCIDENT_CACHE_DIR: Path = STORE_PATH.parent / "investigations" / "vercel"


def _json_echo(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def _die(message: str) -> None:
    print(f"  error: {message}", file=sys.stderr)
    raise SystemExit(1)


def _repair_hint() -> str:
    return (
        "Run 'opensre integrations verify vercel' to confirm the token, or "
        "'opensre integrations setup vercel' to replace it."
    )


def _summarize_error(value: str, *, limit: int = 70) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _format_timestamp(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.isdigit():
        timestamp = int(raw)
        if timestamp > 10_000_000_000:
            timestamp = timestamp // 1000
        return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    return parsed.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _incident_payload(candidate: VercelInvestigationCandidate) -> dict[str, Any]:
    raw_alert = candidate.raw_alert
    project_name = str(raw_alert.get("vercel_project_name") or candidate.pipeline_name).strip()
    deployment_id = str(raw_alert.get("vercel_deployment_id") or candidate.dedupe_key).strip()
    deployment_state = str(raw_alert.get("vercel_deployment_state", "")).strip()
    error_message = str(raw_alert.get("error_message", "")).strip()
    vercel_url = str(raw_alert.get("vercel_url", "")).strip()
    cached_report = _incident_report_path(candidate)
    return {
        "project": project_name,
        "deployment_id": deployment_id,
        "state": deployment_state,
        "severity": candidate.severity,
        "error": error_message,
        "vercel_url": vercel_url,
        "cached_rca_path": str(cached_report) if cached_report.exists() else None,
        "raw_alert": raw_alert,
    }


def _incident_label(candidate: VercelInvestigationCandidate) -> str:
    payload = _incident_payload(candidate)
    project = payload["project"] or "vercel"
    deployment_id = payload["deployment_id"] or "unknown-deployment"
    state = payload["state"] or "UNKNOWN"
    error = _summarize_error(str(payload["error"] or "No error summary"))
    return f"{project} | {deployment_id} | {state} | {error}"


def _project_label(project: dict[str, Any]) -> str:
    name = str(project.get("name", "")).strip() or str(project.get("id", "unknown")).strip()
    framework = str(project.get("framework", "")).strip()
    updated_at = _format_timestamp(project.get("updated_at", ""))
    parts = [name]
    if framework:
        parts.append(framework)
    if updated_at:
        parts.append(f"updated {updated_at}")
    return " | ".join(parts)


def _project_allowlist(project: dict[str, Any]) -> tuple[str, ...]:
    project_id = str(project.get("id", "")).strip()
    project_name = str(project.get("name", "")).strip()
    return tuple(item for item in (project_id, project_name) if item)


def _candidate_created_at(candidate: VercelInvestigationCandidate) -> int:
    raw_value = str(candidate.raw_alert.get("vercel_deployment_created_at", "")).strip()
    return int(raw_value) if raw_value.isdigit() else 0


def _incident_report_path(candidate: VercelInvestigationCandidate) -> Path:
    deployment_id = str(
        candidate.raw_alert.get("vercel_deployment_id") or candidate.dedupe_key or "unknown"
    ).strip()
    return _INCIDENT_CACHE_DIR / f"{deployment_id}.md"


def _render_report(candidate: VercelInvestigationCandidate, report_text: str) -> None:
    print()
    print(f"  RCA for {_incident_label(candidate)}")
    print()
    for line in report_text.strip().splitlines():
        print(f"  {line}")
    print()


def _load_cached_report(candidate: VercelInvestigationCandidate) -> str | None:
    report_path = _incident_report_path(candidate)
    if not report_path.exists():
        return None
    return report_path.read_text(encoding="utf-8")


def _save_report(candidate: VercelInvestigationCandidate, result: dict[str, Any]) -> Path:
    ts = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    raw_alert = candidate.raw_alert
    project_name = str(raw_alert.get("vercel_project_name") or candidate.pipeline_name).strip()
    deployment_id = str(raw_alert.get("vercel_deployment_id") or candidate.dedupe_key).strip()
    vercel_url = str(raw_alert.get("vercel_url", "")).strip()

    if result.get("is_noise"):
        root_cause = "Alert classified as noise - no investigation performed."
        report = "The alert was automatically classified as noise during extraction."
        problem_md = result.get("problem_md") or "N/A"
    else:
        root_cause = result.get("root_cause") or "N/A"
        report = result.get("report") or "N/A"
        problem_md = result.get("problem_md") or "N/A"

    md = (
        f"# Vercel RCA: {project_name or 'vercel'}\n"
        f"Deployment: {deployment_id or 'unknown'}\n"
        f"Severity: {candidate.severity}\n"
        f"Generated: {ts}\n"
        f"Vercel URL: {vercel_url or 'N/A'}\n\n"
        f"## Root Cause\n{root_cause}\n\n"
        f"## Report\n{report}\n\n"
        f"## Problem Description\n{problem_md}\n"
    )
    report_path = _incident_report_path(candidate)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(md, encoding="utf-8")
    return report_path


def _execute_rca(candidate: VercelInvestigationCandidate) -> None:
    print()
    print(f"  Executing RCA for {candidate.pipeline_name} ({candidate.dedupe_key})...")
    result = (
        run_investigation_cli(
            raw_alert=candidate.raw_alert,
            alert_name=candidate.alert_name,
            pipeline_name=candidate.pipeline_name,
            severity=candidate.severity,
        )
        if is_json_output()
        else run_investigation_cli_streaming(
            raw_alert=candidate.raw_alert,
            alert_name=candidate.alert_name,
            pipeline_name=candidate.pipeline_name,
            severity=candidate.severity,
        )
    )
    report_path = _save_report(candidate, result)
    if is_json_output():
        _json_echo({"incident": _incident_payload(candidate), "result": result, "saved_to": str(report_path)})
        return

    print()
    print(f"  Saved RCA report -> {report_path}")
    _render_report(candidate, report_path.read_text(encoding="utf-8"))


def _incident_actions(candidate: VercelInvestigationCandidate) -> str:
    while True:
        cached_report = _load_cached_report(candidate)
        action = questionary.select(
            "Choose an action:",
            choices=[
                questionary.Choice("View RCA", value="view"),
                questionary.Choice("Execute RCA", value="run"),
                questionary.Choice("Back to incidents", value="back"),
                questionary.Choice("Exit", value="exit"),
            ],
        ).ask()

        if action is None or action == "exit":
            return "exit"
        if action == "back":
            return "back"
        if action == "view":
            if cached_report is None:
                print()
                print("  No saved RCA for this incident yet. Choose 'Execute RCA' first.")
                print()
                continue
            _render_report(candidate, cached_report)
            continue
        if action == "run":
            _execute_rca(candidate)


def _load_projects() -> list[dict[str, Any]]:
    config = resolve_vercel_config()
    if config is None:
        raise VercelResolutionError(
            "Vercel integration is not configured. "
            "Set Vercel credentials before browsing incidents."
        )

    client = make_vercel_client(config.api_token, config.team_id)
    if client is None:
        raise VercelResolutionError("Vercel integration is not configured.")

    with client:
        projects_result = client.list_projects(limit=100)
    if not projects_result.get("success"):
        raise VercelResolutionError(
            "Failed to list Vercel projects: "
            f"{projects_result.get('error', 'unknown error')}"
        )

    raw_projects = projects_result.get("projects", [])
    projects = [project for project in raw_projects if isinstance(project, dict)]
    return sorted(projects, key=lambda project: str(project.get("name", "")).strip().lower())


def _select_project(projects: list[dict[str, Any]]) -> dict[str, Any] | None:
    project_by_id = {
        str(project.get("id", "")).strip(): project
        for project in projects
        if str(project.get("id", "")).strip()
    }
    choices = [
        questionary.Choice(_project_label(project), value=str(project.get("id", "")).strip())
        for project in projects
        if str(project.get("id", "")).strip()
    ]
    choices.extend(
        [
            questionary.Separator(),
            questionary.Choice("Exit", value="_exit"),
        ]
    )
    selected = questionary.select("Select a Vercel project:", choices=choices).ask()
    if selected is None or selected == "_exit":
        return None
    if isinstance(selected, str):
        return project_by_id.get(selected)
    return None


def _select_incident(
    candidates: list[VercelInvestigationCandidate],
) -> VercelInvestigationCandidate | str | None:
    candidate_by_key = {candidate.dedupe_key: candidate for candidate in candidates}
    choices = [
        questionary.Choice(_incident_label(candidate), value=candidate.dedupe_key)
        for candidate in candidates
    ]
    choices.extend(
        [
            questionary.Separator(),
            questionary.Choice("Refresh incidents", value="_refresh"),
            questionary.Choice("Exit", value="_exit"),
        ]
    )
    while True:
        selected = questionary.select("Select a Vercel incident:", choices=choices).ask()
        if selected is None or selected == "_exit":
            return None
        if selected == "_refresh":
            return "_refresh"
        if isinstance(selected, str):
            candidate = candidate_by_key.get(selected)
            if candidate is not None:
                return candidate


def _load_incidents(
    limit: int,
    *,
    project_allowlist: tuple[str, ...] = (),
) -> list[VercelInvestigationCandidate]:
    candidates = collect_vercel_candidates(
        project_allowlist=project_allowlist,
        deployment_limit=max(limit, 1),
        fail_on_error=True,
    )
    return sorted(candidates, key=_candidate_created_at, reverse=True)[:limit]


def _load_incidents_with_status(
    limit: int,
    *,
    project_name: str,
    project_allowlist: tuple[str, ...] = (),
) -> list[VercelInvestigationCandidate]:
    from rich.console import Console
    from rich.status import Status

    console = Console(highlight=False)
    with Status(
        f"  Loading incidents for {project_name}...",
        console=console,
        spinner="dots",
    ):
        return _load_incidents(limit, project_allowlist=project_allowlist)


def cmd_vercel_incidents(*, limit: int = 20) -> None:
    """Browse recent Vercel incidents and view or execute RCA from the CLI."""
    try:
        if is_json_output():
            candidates = _load_incidents(limit)
            _json_echo([_incident_payload(candidate) for candidate in candidates])
            return

        projects = _load_projects()
    except VercelResolutionError as exc:
        _die(f"{exc} {_repair_hint()}")
        return

    if not projects:
        print("  No Vercel projects were found for the configured credentials.")
        return

    selected_project = _select_project(projects)
    if selected_project is None:
        return

    project_name = str(selected_project.get("name", "")).strip() or "selected project"
    project_allowlist = _project_allowlist(selected_project)

    try:
        candidates = _load_incidents_with_status(
            limit,
            project_name=project_name,
            project_allowlist=project_allowlist,
        )
    except VercelResolutionError as exc:
        _die(f"{exc} {_repair_hint()}")
        return

    if not candidates:
        print(f"  No actionable Vercel incidents were found for {project_name}.")
        return

    while True:
        selected = _select_incident(candidates)
        if selected is None:
            return
        if selected == "_refresh":
            try:
                refreshed = _load_incidents_with_status(
                    limit,
                    project_name=project_name,
                    project_allowlist=project_allowlist,
                )
            except VercelResolutionError as exc:
                _die(f"{exc} {_repair_hint()}")
                return
            if not refreshed:
                print(f"  No actionable Vercel incidents were found for {project_name}.")
                return
            candidates = refreshed
            continue
        if not isinstance(selected, VercelInvestigationCandidate):
            return
        if _incident_actions(selected) == "exit":
            return

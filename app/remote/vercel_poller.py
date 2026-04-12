"""Vercel URL resolution and polling helpers for remote investigations."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.integrations.verify import resolve_effective_integrations
from app.services.vercel import VercelClient, VercelConfig, make_vercel_client

logger = logging.getLogger(__name__)

_DEFAULT_POLL_INTERVAL_SECONDS = 300
_DEFAULT_DEPLOYMENT_LIMIT = 10
_DEFAULT_LOG_LIMIT = 100
_DEFAULT_PROJECT_FETCH_LIMIT = 100
_ERROR_KEYWORDS = ("error", "failed", "exception", "fatal", "panic", "crash", "unhandled")
_POLL_STATE_FILENAME = "vercel_poll_state.json"


class VercelResolutionError(RuntimeError):
    """Raised when a Vercel URL or deployment cannot be resolved."""


@dataclass(frozen=True)
class ParsedVercelUrl:
    """Normalized details extracted from a user-facing Vercel URL."""

    original_url: str
    team_slug: str = ""
    project_slug: str = ""
    deployment_id: str = ""
    selected_log_id: str = ""


@dataclass(frozen=True)
class VercelInvestigationCandidate:
    """A normalized RCA request derived from Vercel deployment data."""

    dedupe_key: str
    signature: str
    raw_alert: dict[str, Any]
    alert_name: str
    pipeline_name: str
    severity: str


@dataclass(frozen=True)
class VercelPollerSettings:
    """Runtime settings for the background Vercel poller."""

    enabled: bool
    interval_seconds: int
    project_allowlist: tuple[str, ...]
    deployment_limit: int
    log_limit: int

    @classmethod
    def from_env(cls) -> VercelPollerSettings:
        """Build poller settings from environment variables."""
        return cls(
            enabled=_bool_env("VERCEL_POLL_ENABLED"),
            interval_seconds=_int_env(
                "VERCEL_POLL_INTERVAL_SECONDS",
                _DEFAULT_POLL_INTERVAL_SECONDS,
                minimum=30,
            ),
            project_allowlist=_split_csv(os.getenv("VERCEL_POLL_PROJECT_IDS", "")),
            deployment_limit=_int_env(
                "VERCEL_POLL_DEPLOYMENT_LIMIT",
                _DEFAULT_DEPLOYMENT_LIMIT,
                minimum=1,
            ),
            log_limit=_int_env("VERCEL_POLL_LOG_LIMIT", _DEFAULT_LOG_LIMIT, minimum=1),
        )


class VercelPollStateStore:
    """Simple JSON-backed dedupe state for the background poller."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, str]:
        """Return the processed deployment signatures map."""
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError:
            logger.warning("Ignoring invalid Vercel poller state at %s", self.path)
            return {}

        signatures = payload.get("processed_signatures", {})
        if not isinstance(signatures, dict):
            return {}
        return {str(key): str(value) for key, value in signatures.items()}

    def mark_processed(self, dedupe_key: str, signature: str) -> None:
        """Persist a deployment signature after a successful investigation."""
        state = self.load()
        state[dedupe_key] = signature
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"processed_signatures": state}
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


HandleCandidate = Callable[[VercelInvestigationCandidate], Awaitable[bool]]


def _split_csv(value: str) -> tuple[str, ...]:
    items = [part.strip() for part in value.split(",") if part.strip()]
    return tuple(items)


def _bool_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, minimum: int) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        return max(minimum, int(raw_value))
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using default %s", name, raw_value, default)
        return default


def _split_repo_full_name(value: str) -> tuple[str, str]:
    cleaned = value.strip().strip("/")
    if cleaned.count("/") < 1:
        return "", ""
    owner, repo = cleaned.split("/", 1)
    return owner.strip(), repo.strip().removesuffix(".git")


def _extract_meta_field(meta: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = meta.get(key)
        if value:
            return str(value).strip()
    return ""


def _extract_log_message(log: dict[str, Any]) -> str:
    message = log.get("message")
    if message:
        return str(message)
    payload = log.get("payload")
    if isinstance(payload, dict):
        for key in ("text", "message", "body"):
            value = payload.get(key)
            if value:
                return str(value)
    if payload and not isinstance(payload, dict):
        return str(payload)
    return ""


def _has_error_text(value: str) -> bool:
    lowered = value.lower()
    return any(keyword in lowered for keyword in _ERROR_KEYWORDS)


def _error_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if _has_error_text(str(event.get("text", "")))]


def _error_logs(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [log for log in logs if _runtime_log_is_error(log)]


def _runtime_log_is_error(log: dict[str, Any]) -> bool:
    level = str(log.get("level", "") or log.get("type", "")).strip().lower()
    if level in {"error", "fatal"}:
        return True

    status_code = str(log.get("status_code", "")).strip()
    if status_code.isdigit() and int(status_code) >= 500:
        return True

    return _has_error_text(_extract_log_message(log))


def _runtime_log_line(log: dict[str, Any]) -> str:
    message = _extract_log_message(log)
    if not message:
        return ""
    log_type = str(log.get("type", "")).strip()
    source = str(log.get("source", "")).strip()
    prefix = " ".join(part for part in (log_type, source) if part)
    return f"{prefix}: {message}" if prefix else message


def _build_log_excerpt(
    *,
    error_events: list[dict[str, Any]],
    runtime_logs: list[dict[str, Any]],
    selected_log_id: str = "",
) -> str:
    lines: list[str] = []

    if selected_log_id:
        for log in runtime_logs:
            if str(log.get("id", "")).strip() != selected_log_id:
                continue
            line = _runtime_log_line(log)
            if line:
                lines.append(f"selectedLogId={selected_log_id} {line}")
            break

    for event in error_events[:3]:
        text = str(event.get("text", "")).strip()
        if text:
            lines.append(text)

    for log in _error_logs(runtime_logs)[:3]:
        line = _runtime_log_line(log)
        if line:
            lines.append(line)

    unique_lines: list[str] = []
    for line in lines:
        if line and line not in unique_lines:
            unique_lines.append(line)

    return "\n".join(unique_lines[:5])


def _build_failed_steps(deployment: dict[str, Any]) -> str:
    parts = [
        f"deployment_id={deployment.get('id', '')}",
        f"state={deployment.get('state', '')}",
    ]
    error = str(deployment.get("error", "")).strip()
    if error:
        parts.append(f"error={error}")
    return " | ".join(part for part in parts if part)


def _build_repo_url(repo_full_name: str) -> str:
    if not repo_full_name:
        return ""
    return f"https://github.com/{repo_full_name}"


def parse_vercel_url(vercel_url: str) -> ParsedVercelUrl:
    """Parse a user-facing Vercel URL into stable routing identifiers."""
    cleaned = vercel_url.strip()
    parsed = urlparse(cleaned)
    if not cleaned:
        raise VercelResolutionError("Vercel URL is required.")
    if "vercel.com" not in parsed.netloc.lower():
        raise VercelResolutionError(f"Unsupported Vercel URL host: {parsed.netloc or '<empty>'}")

    parts = [part for part in parsed.path.split("/") if part]
    team_slug = ""
    project_slug = ""
    deployment_id = ""

    if len(parts) >= 3 and parts[-1] == "logs":
        team_slug = parts[-3]
        project_slug = parts[-2]
    elif "deployments" in parts:
        deployments_index = parts.index("deployments")
        if deployments_index >= 2:
            team_slug = parts[deployments_index - 2]
            project_slug = parts[deployments_index - 1]
        if deployments_index + 1 < len(parts):
            deployment_id = parts[deployments_index + 1]
    elif len(parts) >= 2:
        team_slug, project_slug = parts[0], parts[1]

    query = parse_qs(parsed.query)
    selected_log_id = (
        query.get("selectedLogId", [""])[0]
        or query.get("logId", [""])[0]
        or query.get("selectedLog", [""])[0]
    ).strip()
    deployment_id = (
        deployment_id
        or query.get("deploymentId", [""])[0].strip()
        or query.get("deployment_id", [""])[0].strip()
    )

    return ParsedVercelUrl(
        original_url=cleaned,
        team_slug=team_slug.strip(),
        project_slug=project_slug.strip(),
        deployment_id=deployment_id,
        selected_log_id=selected_log_id,
    )


def resolve_vercel_config() -> VercelConfig | None:
    """Resolve the effective Vercel integration config from env or local store."""
    effective_integrations = resolve_effective_integrations()
    vercel_entry = effective_integrations.get("vercel", {})
    config = vercel_entry.get("config", {}) if isinstance(vercel_entry, dict) else {}
    if not isinstance(config, dict):
        return None
    try:
        return VercelConfig.model_validate(config)
    except Exception as exc:  # pragma: no cover - defensive validation wrapper
        logger.warning("Failed to resolve Vercel config: %s", exc)
        return None


def _make_client_from_config(config: VercelConfig) -> VercelClient:
    client = make_vercel_client(config.api_token, config.team_id)
    if client is None:
        raise VercelResolutionError("Vercel integration is not configured on this server.")
    return client


def _deployment_created_sort_key(stub: dict[str, Any]) -> str:
    raw = str(stub.get("created_at", "")).strip()
    return raw or "1970-01-01T00:00:00.000Z"


def _sort_deployment_stubs_newest_first(stubs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = [s for s in stubs if isinstance(s, dict) and str(s.get("id", "")).strip()]
    return sorted(filtered, key=_deployment_created_sort_key, reverse=True)


def _resolve_project(
    client: VercelClient,
    *,
    project_id: str = "",
    project_slug: str = "",
) -> dict[str, Any]:
    projects_result = client.list_projects(limit=_DEFAULT_PROJECT_FETCH_LIMIT)
    if not projects_result.get("success"):
        raise VercelResolutionError(
            f"Failed to list Vercel projects: {projects_result.get('error', 'unknown error')}"
        )

    raw_projects = projects_result.get("projects", [])
    projects = [project for project in raw_projects if isinstance(project, dict)]
    normalized_slug = project_slug.strip().lower()
    normalized_id = project_id.strip()

    for project in projects:
        current_id = str(project.get("id", "")).strip()
        current_name = str(project.get("name", "")).strip()
        if normalized_id and current_id == normalized_id:
            return project
        if normalized_slug and current_name.lower() == normalized_slug:
            return project

    if normalized_id:
        raise VercelResolutionError(f"Vercel project {normalized_id!r} was not found.")
    raise VercelResolutionError(f"Vercel project {project_slug!r} was not found.")


def _parallel_deployment_events_and_runtime_logs(
    config: VercelConfig,
    *,
    project_id: str,
    deployment_id: str,
    log_limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch build events and runtime logs concurrently (separate clients — httpx is not thread-safe)."""

    def _events() -> list[dict[str, Any]]:
        with _make_client_from_config(config) as worker:
            result = worker.get_deployment_events(deployment_id, limit=log_limit)
            return result.get("events", []) if result.get("success") else []

    def _runtime() -> list[dict[str, Any]]:
        with _make_client_from_config(config) as worker:
            result = worker.get_runtime_logs(
                deployment_id,
                limit=log_limit,
                project_id=project_id,
            )
            return result.get("logs", []) if result.get("success") else []

    with ThreadPoolExecutor(max_workers=2) as pool:
        events_future = pool.submit(_events)
        logs_future = pool.submit(_runtime)
        return events_future.result(), logs_future.result()


def _fetch_deployment_bundle(
    client: VercelClient,
    *,
    project_id: str,
    deployment_id: str,
    log_limit: int,
    include_runtime_logs: bool = True,
    parallel_fetch: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    deployment_result = client.get_deployment(deployment_id)
    if not deployment_result.get("success"):
        raise VercelResolutionError(
            f"Failed to fetch Vercel deployment {deployment_id!r}: "
            f"{deployment_result.get('error', 'unknown error')}"
        )

    deployment = deployment_result.get("deployment", {})
    config = getattr(client, "config", None)
    if include_runtime_logs and parallel_fetch and isinstance(config, VercelConfig):
        events, runtime_logs = _parallel_deployment_events_and_runtime_logs(
            config,
            project_id=project_id,
            deployment_id=deployment_id,
            log_limit=log_limit,
        )
        return deployment, events, runtime_logs

    events_result = client.get_deployment_events(deployment_id, limit=log_limit)
    events = events_result.get("events", []) if events_result.get("success") else []
    if not include_runtime_logs:
        return deployment, events, []

    runtime_logs_result = client.get_runtime_logs(
        deployment_id,
        limit=log_limit,
        project_id=project_id,
    )
    runtime_logs = runtime_logs_result.get("logs", []) if runtime_logs_result.get("success") else []
    return deployment, events, runtime_logs


def _find_deployment_by_selected_log_id(
    client: VercelClient,
    *,
    project_id: str,
    selected_log_id: str,
    deployment_limit: int,
    log_limit: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    deployments_result = client.list_deployments(project_id=project_id, limit=deployment_limit)
    if not deployments_result.get("success"):
        raise VercelResolutionError(
            "Failed to list Vercel deployments while resolving log URL: "
            f"{deployments_result.get('error', 'unknown error')}"
        )

    for deployment in deployments_result.get("deployments", []):
        deployment_id = str(deployment.get("id", "")).strip()
        if not deployment_id:
            continue
        deployment_details, events, runtime_logs = _fetch_deployment_bundle(
            client,
            project_id=project_id,
            deployment_id=deployment_id,
            log_limit=log_limit,
        )
        if any(str(log.get("id", "")).strip() == selected_log_id for log in runtime_logs):
            return deployment_details, events, runtime_logs
        if any(str(event.get("id", "")).strip() == selected_log_id for event in events):
            return deployment_details, events, runtime_logs

    raise VercelResolutionError(
        "Could not resolve the pasted Vercel URL to a recent deployment. "
        "Try providing the deployment URL or deployment ID directly."
    )


def _select_latest_actionable_deployment(
    client: VercelClient,
    *,
    project_id: str,
    deployment_limit: int,
    log_limit: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    deployments_result = client.list_deployments(project_id=project_id, limit=deployment_limit)
    if not deployments_result.get("success"):
        raise VercelResolutionError(
            f"Failed to list Vercel deployments: {deployments_result.get('error', 'unknown error')}"
        )

    for deployment in deployments_result.get("deployments", []):
        deployment_id = str(deployment.get("id", "")).strip()
        if not deployment_id:
            continue
        deployment_details, events, runtime_logs = _fetch_deployment_bundle(
            client,
            project_id=project_id,
            deployment_id=deployment_id,
            log_limit=log_limit,
        )
        if _deployment_is_actionable(deployment_details, events, runtime_logs):
            return deployment_details, events, runtime_logs

    raise VercelResolutionError(
        "No actionable Vercel deployments were found for the target project."
    )


def _deployment_is_actionable(
    deployment: dict[str, Any],
    events: list[dict[str, Any]],
    runtime_logs: list[dict[str, Any]],
) -> bool:
    state = str(deployment.get("state", "")).strip().upper()
    if state in {"ERROR", "CANCELED"}:
        return True
    if str(deployment.get("error", "")).strip():
        return True
    if _error_events(events):
        return True
    return bool(_error_logs(runtime_logs))


def _build_signature(
    deployment: dict[str, Any],
    error_events: list[dict[str, Any]],
    runtime_logs: list[dict[str, Any]],
    *,
    selected_log_id: str = "",
) -> str:
    latest_runtime_log_id = ""
    if runtime_logs:
        latest_runtime_log_id = str(runtime_logs[0].get("id", "")).strip()

    payload = {
        "deployment_id": str(deployment.get("id", "")).strip(),
        "state": str(deployment.get("state", "")).strip(),
        "error": str(deployment.get("error", "")).strip(),
        "selected_log_id": selected_log_id,
        "top_error_event": str(error_events[0].get("text", "")).strip() if error_events else "",
        "latest_runtime_log_id": latest_runtime_log_id,
    }
    return json.dumps(payload, sort_keys=True, default=str)


def _canonical_vercel_alert(
    *,
    project: dict[str, Any],
    deployment: dict[str, Any],
    events: list[dict[str, Any]],
    runtime_logs: list[dict[str, Any]],
    vercel_url: str = "",
    selected_log_id: str = "",
) -> dict[str, Any]:
    project_id = str(project.get("id", "")).strip()
    project_name = str(project.get("name", "")).strip()
    deployment_id = str(deployment.get("id", "")).strip()
    deployment_state = str(deployment.get("state", "")).strip()
    deployment_error = str(deployment.get("error", "")).strip()
    deployment_created_at = str(
        deployment.get("createdAt") or deployment.get("created_at") or ""
    ).strip()
    meta = deployment.get("meta", {}) if isinstance(deployment.get("meta"), dict) else {}

    repo_full_name = _extract_meta_field(meta, "github_repo", "githubRepo")
    github_sha = _extract_meta_field(meta, "github_commit_sha", "githubCommitSha")
    github_ref = _extract_meta_field(meta, "github_commit_ref", "githubCommitRef")
    github_owner, github_repo = _split_repo_full_name(repo_full_name)

    error_events = _error_events(events)
    runtime_error_logs = _error_logs(runtime_logs)
    primary_error = (
        deployment_error
        or (str(error_events[0].get("text", "")).strip() if error_events else "")
        or (_extract_log_message(runtime_error_logs[0]) if runtime_error_logs else "")
        or "Vercel deployment investigation requested."
    )
    log_excerpt = _build_log_excerpt(
        error_events=error_events,
        runtime_logs=runtime_logs,
        selected_log_id=selected_log_id,
    )

    alert_name = f"Vercel deployment issue: {project_name or deployment.get('name', 'project')}"
    severity = "critical" if deployment_state.upper() in {"ERROR", "CANCELED"} else "warning"

    annotations = {
        "error": primary_error,
        "failed_steps": _build_failed_steps(deployment),
        "log_excerpt": log_excerpt,
        "vercel_project_id": project_id,
        "vercel_project_name": project_name,
        "vercel_project_slug": project_name,
        "vercel_deployment_id": deployment_id,
        "vercel_deployment_state": deployment_state,
        "vercel_deployment_error": deployment_error,
        "vercel_deployment_created_at": deployment_created_at,
        "vercel_selected_log_id": selected_log_id,
        "repository": repo_full_name,
        "repo_url": _build_repo_url(repo_full_name),
        "github_owner": github_owner,
        "github_repo": github_repo,
        "github_sha": github_sha,
        "github_ref": github_ref,
    }

    return {
        "alert_name": alert_name,
        "pipeline_name": project_name or str(deployment.get("name", "")).strip() or "vercel",
        "severity": severity,
        "alert_source": "vercel",
        "text": (
            f"{alert_name}\n"
            f"Project: {project_name or project_id}\n"
            f"Deployment: {deployment_id}\n"
            f"State: {deployment_state or 'unknown'}\n"
            f"Error: {primary_error}\n"
            f"Log Excerpt:\n{log_excerpt}"
        ).strip(),
        "error_message": primary_error,
        "github_query": primary_error,
        "code_query": primary_error,
        "vercel_url": vercel_url,
        "vercel_log_url": vercel_url,
        "vercel_project_id": project_id,
        "vercel_project_name": project_name,
        "vercel_project_slug": project_name,
        "vercel_deployment_id": deployment_id,
        "vercel_deployment_state": deployment_state,
        "vercel_deployment_error": deployment_error,
        "vercel_deployment_created_at": deployment_created_at,
        "vercel_selected_log_id": selected_log_id,
        "repository": repo_full_name,
        "repo_url": _build_repo_url(repo_full_name),
        "github_owner": github_owner,
        "github_repo": github_repo,
        "sha": github_sha,
        "branch": github_ref,
        "annotations": annotations,
        "commonAnnotations": dict(annotations),
    }


def _merge_alerts(
    *,
    canonical: dict[str, Any],
    original: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(canonical)
    merged.update(original)

    canonical_annotations = canonical.get("annotations", {})
    original_annotations = original.get("annotations", {})
    if isinstance(canonical_annotations, dict) or isinstance(original_annotations, dict):
        annotations: dict[str, Any] = {}
        if isinstance(canonical_annotations, dict):
            annotations.update(canonical_annotations)
        if isinstance(original_annotations, dict):
            annotations.update(original_annotations)
        merged["annotations"] = annotations

    canonical_common = canonical.get("commonAnnotations", {})
    original_common = original.get("commonAnnotations", {})
    if isinstance(canonical_common, dict) or isinstance(original_common, dict):
        common_annotations: dict[str, Any] = {}
        if isinstance(canonical_common, dict):
            common_annotations.update(canonical_common)
        if isinstance(original_common, dict):
            common_annotations.update(original_common)
        merged["commonAnnotations"] = common_annotations

    for key, value in canonical.items():
        if key in {"annotations", "commonAnnotations"}:
            continue
        if key not in original:
            continue
        current = merged.get(key)
        if current is None or current == "":
            merged[key] = value

    return merged


def build_vercel_investigation_candidate(
    *,
    project: dict[str, Any],
    deployment: dict[str, Any],
    events: list[dict[str, Any]],
    runtime_logs: list[dict[str, Any]],
    original_alert: dict[str, Any] | None = None,
    vercel_url: str = "",
    selected_log_id: str = "",
) -> VercelInvestigationCandidate:
    """Build a normalized RCA request payload from Vercel deployment data."""
    canonical_alert = _canonical_vercel_alert(
        project=project,
        deployment=deployment,
        events=events,
        runtime_logs=runtime_logs,
        vercel_url=vercel_url,
        selected_log_id=selected_log_id,
    )
    merged_alert = _merge_alerts(canonical=canonical_alert, original=original_alert or {})
    error_events = _error_events(events)
    signature = _build_signature(
        deployment,
        error_events,
        runtime_logs,
        selected_log_id=selected_log_id,
    )
    deployment_id = str(deployment.get("id", "")).strip()
    return VercelInvestigationCandidate(
        dedupe_key=deployment_id,
        signature=signature,
        raw_alert=merged_alert,
        alert_name=str(merged_alert.get("alert_name", "Vercel deployment issue")).strip(),
        pipeline_name=str(merged_alert.get("pipeline_name", "vercel")).strip(),
        severity=str(merged_alert.get("severity", "warning")).strip(),
    )


def enrich_remote_alert_from_vercel(raw_alert: dict[str, Any]) -> dict[str, Any]:
    """Resolve a pasted Vercel URL or deployment reference into a rich alert payload."""
    annotations = raw_alert.get("annotations", {})
    vercel_url = str(
        raw_alert.get("vercel_log_url")
        or raw_alert.get("vercel_url")
        or (annotations.get("vercel_log_url", "") if isinstance(annotations, dict) else "")
        or (annotations.get("vercel_url", "") if isinstance(annotations, dict) else "")
    ).strip()
    project_id = str(
        raw_alert.get("vercel_project_id")
        or (annotations.get("vercel_project_id", "") if isinstance(annotations, dict) else "")
    ).strip()
    deployment_id = str(
        raw_alert.get("vercel_deployment_id")
        or (annotations.get("vercel_deployment_id", "") if isinstance(annotations, dict) else "")
    ).strip()

    if not any((vercel_url, project_id, deployment_id)):
        return raw_alert

    config = resolve_vercel_config()
    if config is None:
        raise VercelResolutionError("Vercel integration is not configured on this server.")

    parsed = parse_vercel_url(vercel_url) if vercel_url else ParsedVercelUrl(original_url="")
    client = _make_client_from_config(config)
    with client:
        project = _resolve_project(
            client,
            project_id=project_id,
            project_slug=parsed.project_slug,
        )

        resolved_deployment_id = deployment_id or parsed.deployment_id
        if resolved_deployment_id:
            deployment, events, runtime_logs = _fetch_deployment_bundle(
                client,
                project_id=str(project.get("id", "")).strip(),
                deployment_id=resolved_deployment_id,
                log_limit=_DEFAULT_LOG_LIMIT,
            )
        elif parsed.selected_log_id:
            deployment, events, runtime_logs = _find_deployment_by_selected_log_id(
                client,
                project_id=str(project.get("id", "")).strip(),
                selected_log_id=parsed.selected_log_id,
                deployment_limit=_DEFAULT_DEPLOYMENT_LIMIT,
                log_limit=_DEFAULT_LOG_LIMIT,
            )
        else:
            deployment, events, runtime_logs = _select_latest_actionable_deployment(
                client,
                project_id=str(project.get("id", "")).strip(),
                deployment_limit=_DEFAULT_DEPLOYMENT_LIMIT,
                log_limit=_DEFAULT_LOG_LIMIT,
            )

    candidate = build_vercel_investigation_candidate(
        project=project,
        deployment=deployment,
        events=events,
        runtime_logs=runtime_logs,
        original_alert=raw_alert,
        vercel_url=parsed.original_url or vercel_url,
        selected_log_id=parsed.selected_log_id,
    )
    return candidate.raw_alert


class VercelPoller:
    """Background poller that turns recent Vercel failures into RCA investigations."""

    def __init__(
        self,
        *,
        investigations_dir: Path,
        settings: VercelPollerSettings | None = None,
    ) -> None:
        self.settings = settings or VercelPollerSettings.from_env()
        self.state_store = VercelPollStateStore(investigations_dir / _POLL_STATE_FILENAME)

    @property
    def is_enabled(self) -> bool:
        return bool(self.settings.enabled and self.settings.project_allowlist)

    def collect_candidates(self) -> list[VercelInvestigationCandidate]:
        """Collect new actionable deployment failures from the configured projects."""
        if not self.is_enabled:
            return []
        processed_signatures = self.state_store.load()
        candidates = collect_vercel_candidates(
            project_allowlist=self.settings.project_allowlist,
            deployment_limit=self.settings.deployment_limit,
            log_limit=self.settings.log_limit,
        )
        return [
            candidate
            for candidate in candidates
            if processed_signatures.get(candidate.dedupe_key) != candidate.signature
        ]

    async def run_forever(self, handle_candidate: HandleCandidate) -> None:
        """Poll Vercel on an interval and investigate new actionable failures."""
        if not self.is_enabled:
            return

        logger.info(
            "Starting Vercel poller: interval=%ss projects=%s",
            self.settings.interval_seconds,
            ",".join(self.settings.project_allowlist),
        )

        while True:
            try:
                candidates = await asyncio.to_thread(self.collect_candidates)
                for candidate in candidates:
                    try:
                        was_processed = await handle_candidate(candidate)
                    except Exception:
                        logger.exception(
                            "Background RCA for Vercel deployment %s failed",
                            candidate.dedupe_key,
                        )
                        was_processed = False
                    if was_processed:
                        await asyncio.to_thread(
                            self.state_store.mark_processed,
                            candidate.dedupe_key,
                            candidate.signature,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Vercel poller iteration failed")

            await asyncio.sleep(self.settings.interval_seconds)


def collect_vercel_candidates(
    *,
    project_allowlist: tuple[str, ...] = (),
    deployment_limit: int = _DEFAULT_DEPLOYMENT_LIMIT,
    log_limit: int = _DEFAULT_LOG_LIMIT,
    fail_on_error: bool = False,
    include_runtime_logs: bool = True,
) -> list[VercelInvestigationCandidate]:
    """Collect actionable Vercel deployment failures for background polling (and similar).

    When ``include_runtime_logs`` is False, skip per-deployment runtime log HTTP calls.
    Uses ``list_deployments`` plus per-deployment events/runtime logs (parallel fetch when
    using a real :class:`~app.services.vercel.client.VercelClient`).
    """
    config = resolve_vercel_config()
    if config is None:
        if fail_on_error:
            raise VercelResolutionError("Vercel integration is not configured.")
        logger.warning(
            "Skipping Vercel incident collection because the integration is not configured."
        )
        return []

    allowlist = {item.lower() for item in project_allowlist}
    client = _make_client_from_config(config)
    with client:
        projects_result = client.list_projects(limit=_DEFAULT_PROJECT_FETCH_LIMIT)
        if not projects_result.get("success"):
            if fail_on_error:
                raise VercelResolutionError(
                    "Failed to list Vercel projects: "
                    f"{projects_result.get('error', 'unknown error')}"
                )
            logger.warning(
                "Skipping Vercel incident collection because projects could not be listed: %s",
                projects_result.get("error", "unknown error"),
            )
            return []

        raw_projects = projects_result.get("projects", [])
        projects = [
            project
            for project in raw_projects
            if isinstance(project, dict)
            and (
                not allowlist
                or str(project.get("id", "")).strip().lower() in allowlist
                or str(project.get("name", "")).strip().lower() in allowlist
            )
        ]

        candidates: list[VercelInvestigationCandidate] = []
        for project in projects:
            list_limit = (
                min(100, max(25, deployment_limit * 25))
                if deployment_limit <= 5
                else min(100, deployment_limit)
            )
            deployments_result = client.list_deployments(
                project_id=str(project.get("id", "")).strip(),
                limit=list_limit,
            )
            if not deployments_result.get("success"):
                if fail_on_error:
                    raise VercelResolutionError(
                        f"Failed to list deployments for Vercel project "
                        f"{project.get('name', project.get('id', 'unknown'))}: "
                        f"{deployments_result.get('error', 'unknown error')}"
                    )
                logger.warning(
                    "Skipping Vercel project %s because deployments could not be listed: %s",
                    project.get("name", project.get("id", "unknown")),
                    deployments_result.get("error", "unknown error"),
                )
                continue

            ordered_stubs = _sort_deployment_stubs_newest_first(
                [d for d in deployments_result.get("deployments", []) if isinstance(d, dict)]
            )
            for deployment_stub in ordered_stubs[:deployment_limit]:
                deployment_id = str(deployment_stub.get("id", "")).strip()
                if not deployment_id:
                    continue
                deployment, events, runtime_logs = _fetch_deployment_bundle(
                    client,
                    project_id=str(project.get("id", "")).strip(),
                    deployment_id=deployment_id,
                    log_limit=log_limit,
                    include_runtime_logs=include_runtime_logs,
                )
                if not _deployment_is_actionable(deployment, events, runtime_logs):
                    continue

                candidates.append(
                    build_vercel_investigation_candidate(
                        project=project,
                        deployment=deployment,
                        events=events,
                        runtime_logs=runtime_logs,
                    )
                )

    return candidates

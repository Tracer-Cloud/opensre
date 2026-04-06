"""Shared gitlab integration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import Field, field_validator

from app.strict_config import StrictConfigModel

DEFAULT_GITLAB_BASE_URL = "https://gitlab.com/api/v4"


class GitlabConfig(StrictConfigModel):
    """Normalized Gitlab connection settings."""

    base_url: str = DEFAULT_GITLAB_BASE_URL
    auth_token: str = ""
    timeout_seconds: float = Field(default=15.0, gt=0)

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_GITLAB_BASE_URL).strip()
        return normalized or DEFAULT_GITLAB_BASE_URL

    @property
    def api_base_url(self) -> str:
        return self.base_url.rstrip("/")

    @property
    def auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.auth_token}",
            "Accept": "application/json",
        }


@dataclass(frozen=True)
class GitlabValidationResult:
    """Result of validating a Gitlab integration."""

    ok: bool
    detail: str


def build_gitlab_config(raw: dict[str, Any] | None) -> GitlabConfig:
    """Build a normalized Gitlab config object from env/store data."""
    return GitlabConfig.model_validate(raw or {})


def gitlab_config_from_env() -> GitlabConfig | None:
    """Load a Gitlab config from env vars."""
    auth_token = os.getenv("GITLAB_ACCESS_TOKEN", "").strip()
    if not auth_token:
        return None
    return build_gitlab_config({
        "base_url": os.getenv("GITLAB_BASE_URL", DEFAULT_GITLAB_BASE_URL).strip() or DEFAULT_GITLAB_BASE_URL,
        "auth_token": auth_token,
    })


def _request_json(
    config: GitlabConfig,
    method: str,
    path: str,
    *,
    params: list[tuple[str, str | int | float | bool | None]] | None = None,
    json: dict | None = None,
) -> Any:
    url = f"{config.api_base_url}{path}"
    response = httpx.request(
        method,
        url,
        json=json,
        headers=config.auth_headers,
        params=params,
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def validate_gitlab_config(config: GitlabConfig) -> GitlabValidationResult:
    """Validate gitlab connectivity with a lightweight user query."""

    if not config.auth_token:
        return GitlabValidationResult(ok=False, detail="Gitlab auth token is required.")

    try:
        user = validate_gitlab_connection(config=config)
        username = user.get("username", "unknown")
        return GitlabValidationResult(
            ok=True,
            detail=f"GitLab connectivity successful. Authenticated as @{username}"
        )
    except httpx.HTTPStatusError as err:
        detail = err.response.text.strip() or str(err)
        return GitlabValidationResult(ok=False, detail=f"Gitlab validation failed: {detail}")
    except Exception as err:  # noqa: BLE001
        return GitlabValidationResult(ok=False, detail=f"Gitlab validation failed: {err}")


def validate_gitlab_connection(
    *,
    config: GitlabConfig,
) -> dict[str, Any]:
    """Validate gitlab connection."""

    payload = _request_json(
        config,
        "GET",
        "/user",
    )
    return payload if isinstance(payload, dict) else {}


def get_gitlab_commits(
    *,
    config: GitlabConfig,
    project_id: str,
    ref_name="main",
    since: str,
    per_page: int = 10
) -> list[dict[str, Any]]:
    """Fetch gitlab commits for project."""

    encoded_project_id = quote(project_id, safe="")
    params: list[tuple[str, str | int | float | bool | None]] = [
        ("ref_name", ref_name),
        ("per_page", per_page),
    ]
    if since:
        params.append(("since", since))
    payload = _request_json(
        config,
        "GET",
        f"/projects/{encoded_project_id}/repository/commits",
        params=params,
    )
    return payload if isinstance(payload, list) else []


def get_gitlab_mrs(
    *,
    config: GitlabConfig,
    project_id: str,
    state: str = "merged",
    target_branch: str = "main",
    updated_after: str,
    per_page: int = 10
) -> list[dict[str, Any]]:
    """Fetch gitlab Merge requests for project."""

    encoded_project_id = quote(project_id, safe="")
    payload = _request_json(
        config,
        "GET",
        f"/projects/{encoded_project_id}/merge_requests",
        params=[
            ("state", state),
            ("updated_after", updated_after),
            ("target_branch", target_branch),
            ("per_page", per_page)
        ],
    )
    return payload if isinstance(payload, list) else []


def get_gitlab_pipelines(
    *,
    config: GitlabConfig,
    project_id: str,
    ref: str = "main",
    status: str = "failed",
    updated_after: str,
    per_page: int = 5,
) -> list[dict[str, Any]]:
    """Fetch gitlab pipelines for project."""

    encoded_project_id = quote(project_id, safe="")
    payload = _request_json(
        config,
        "GET",
        f"/projects/{encoded_project_id}/pipelines",
        params=[
            ("status", status),
            ("updated_after", updated_after),
            ("ref", ref),
            ("per_page", per_page)
        ],
    )
    return payload if isinstance(payload, list) else []


def get_gitlab_file(
    *,
    config: GitlabConfig,
    project_id: str,
    ref: str = "main",
    file_path: str,
) -> dict[str, Any]:
    """Fetch particular gitlab file"""

    encoded_project_id = quote(project_id, safe="")
    encoded_path = quote(file_path, safe='')
    payload = _request_json(
        config,
        "GET",
        f"/projects/{encoded_project_id}/repository/files/{encoded_path}",
        params=[
            ("ref", ref),
        ],
    )
    return payload if isinstance(payload, dict) else {}


def post_gitlab_mr_note(*,
    config: GitlabConfig,
    project_id: str,
    mr_iid: str,
    body: str) -> dict[str, Any]:
    """Post findings back on mr as comment"""

    encoded_project_id = quote(project_id, safe="")
    json_body = {"body": body}
    payload = _request_json(
        config,
        "POST",
        f"/projects/{encoded_project_id}/merge_requests/{mr_iid}/notes",
        json=json_body
    )
    return payload if isinstance(payload, dict) else {}

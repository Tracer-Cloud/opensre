"""GitLab config, HTTP client, and validation — the vendor's leaf core.

Kept below the package root so ``setup.py`` and ``verifier.py`` import their
config/validation helpers from here rather than from ``integrations.gitlab``.
That lets the root re-export ``GITLAB_SETUP`` without a ``root -> setup ->
verifier -> root`` import cycle. The public API is re-exported from the root.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from pydantic import Field, field_validator

from config.constants.gitlab import GITLAB_AUTH_TOKEN_ENV, GITLAB_BASE_URL_ENV
from config.llm_credentials import resolve_env_credential
from config.strict_config import StrictConfigModel
from integrations._validation_helpers import report_classify_failure, report_validation_failure

logger = logging.getLogger(__name__)

DEFAULT_GITLAB_BASE_URL = "https://gitlab.com/api/v4"


def _normalize_gitlab_base_url(value: Any) -> str:
    normalized = str(value or DEFAULT_GITLAB_BASE_URL).strip().rstrip("/")
    if not normalized:
        return DEFAULT_GITLAB_BASE_URL

    parsed = urlsplit(normalized)
    if parsed.scheme and parsed.netloc and parsed.path == "":
        return f"{normalized}/api/v4"
    return normalized


class GitlabConfig(StrictConfigModel):
    """Normalized Gitlab connection settings."""

    base_url: str = DEFAULT_GITLAB_BASE_URL
    auth_token: str = ""
    timeout_seconds: float = Field(default=15.0, gt=0)

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        return _normalize_gitlab_base_url(value)

    @property
    def api_base_url(self) -> str:
        return self.base_url.rstrip("/")

    @property
    def auth_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers


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
    auth_token = resolve_env_credential(GITLAB_AUTH_TOKEN_ENV)
    if not auth_token:
        return None
    return build_gitlab_config(
        {
            "base_url": os.getenv(GITLAB_BASE_URL_ENV, DEFAULT_GITLAB_BASE_URL).strip()
            or DEFAULT_GITLAB_BASE_URL,
            "auth_token": auth_token,
        }
    )


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
            ok=True, detail=f"GitLab connectivity successful. Authenticated as @{username}"
        )
    except httpx.HTTPStatusError as err:
        detail = err.response.text.strip() or str(err)
        return GitlabValidationResult(ok=False, detail=f"Gitlab validation failed: {detail}")
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="gitlab",
            method="validate_gitlab_config",
        )
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
    *, config: GitlabConfig, project_id: str, ref_name="main", since: str, per_page: int = 10
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
    per_page: int = 10,
) -> list[dict[str, Any]]:
    """Fetch gitlab Merge requests for project."""

    encoded_project_id = quote(project_id, safe="")
    params: list[tuple[str, str | int | float | bool | None]] = [
        ("state", state),
        ("target_branch", target_branch),
        ("per_page", per_page),
    ]
    if updated_after:
        params.append(("updated_after", updated_after))
    payload = _request_json(
        config,
        "GET",
        f"/projects/{encoded_project_id}/merge_requests",
        params=params,
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
    params: list[tuple[str, str | int | float | bool | None]] = [
        ("status", status),
        ("ref", ref),
        ("per_page", per_page),
    ]
    if updated_after:
        params.append(("updated_after", updated_after))
    payload = _request_json(
        config,
        "GET",
        f"/projects/{encoded_project_id}/pipelines",
        params=params,
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
    encoded_path = quote(file_path, safe="")
    payload = _request_json(
        config,
        "GET",
        f"/projects/{encoded_project_id}/repository/files/{encoded_path}",
        params=[
            ("ref", ref),
        ],
    )
    return payload if isinstance(payload, dict) else {}


def post_gitlab_mr_note(
    *, config: GitlabConfig, project_id: str, mr_iid: str, body: str
) -> dict[str, Any]:
    """Post findings back on mr as comment"""

    encoded_project_id = quote(project_id, safe="")
    json_body = {"body": body}
    payload = _request_json(
        config,
        "POST",
        f"/projects/{encoded_project_id}/merge_requests/{mr_iid}/notes",
        json=json_body,
    )
    return payload if isinstance(payload, dict) else {}


def classify(credentials: dict[str, Any], record_id: str) -> tuple[GitlabConfig | None, str | None]:
    try:
        cfg = build_gitlab_config(
            {
                "base_url": credentials.get("base_url", ""),
                "auth_token": credentials.get("auth_token", ""),
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="gitlab", record_id=record_id)
        return None, None
    return cfg, "gitlab"


__all__ = [
    "DEFAULT_GITLAB_BASE_URL",
    "GitlabConfig",
    "GitlabValidationResult",
    "build_gitlab_config",
    "classify",
    "get_gitlab_commits",
    "get_gitlab_file",
    "get_gitlab_mrs",
    "get_gitlab_pipelines",
    "gitlab_config_from_env",
    "post_gitlab_mr_note",
    "validate_gitlab_config",
    "validate_gitlab_connection",
]

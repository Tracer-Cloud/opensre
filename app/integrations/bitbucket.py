"""Shared Bitbucket integration helpers.

Provides configuration, connectivity validation, and read-only repository
queries for Bitbucket Cloud instances via the REST API v2.0.
All operations are read-only with enforced timeouts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import Field, field_validator

from app.strict_config import StrictConfigModel

DEFAULT_BITBUCKET_BASE_URL = "https://api.bitbucket.org/2.0"
DEFAULT_BITBUCKET_TIMEOUT_SECONDS = 10.0
DEFAULT_BITBUCKET_MAX_RESULTS = 25


class BitbucketConfig(StrictConfigModel):
    """Normalized Bitbucket connection settings."""

    workspace: str = ""
    app_password: str = ""
    username: str = ""
    base_url: str = DEFAULT_BITBUCKET_BASE_URL
    timeout_seconds: float = Field(default=DEFAULT_BITBUCKET_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_BITBUCKET_MAX_RESULTS, gt=0, le=100)
    integration_id: str = ""

    @field_validator("workspace", mode="before")
    @classmethod
    def _normalize_workspace(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_BITBUCKET_BASE_URL).strip().rstrip("/")
        return normalized or DEFAULT_BITBUCKET_BASE_URL

    @property
    def is_configured(self) -> bool:
        return bool(self.workspace and self.app_password and self.username)


@dataclass(frozen=True)
class BitbucketValidationResult:
    """Result of validating a Bitbucket integration."""

    ok: bool
    detail: str


def build_bitbucket_config(raw: dict[str, Any] | None) -> BitbucketConfig:
    """Build a normalized Bitbucket config object from env/store data."""
    return BitbucketConfig.model_validate(raw or {})


def bitbucket_config_from_env() -> BitbucketConfig | None:
    """Load a Bitbucket config from env vars."""
    workspace = os.getenv("BITBUCKET_WORKSPACE", "").strip()
    if not workspace:
        return None
    return build_bitbucket_config(
        {
            "workspace": workspace,
            "username": os.getenv("BITBUCKET_USERNAME", "").strip(),
            "app_password": os.getenv("BITBUCKET_APP_PASSWORD", "").strip(),
            "base_url": os.getenv("BITBUCKET_BASE_URL", DEFAULT_BITBUCKET_BASE_URL).strip(),
        }
    )


def _get_client(config: BitbucketConfig) -> httpx.Client:
    """Create an authenticated httpx client for Bitbucket API calls."""
    return httpx.Client(
        base_url=config.base_url,
        auth=(config.username, config.app_password),
        timeout=config.timeout_seconds,
        headers={"Accept": "application/json"},
    )


def validate_bitbucket_config(
    config: BitbucketConfig,
) -> BitbucketValidationResult:
    """Validate Bitbucket connectivity by fetching the authenticated user."""
    if not config.is_configured:
        return BitbucketValidationResult(
            ok=False,
            detail="Bitbucket workspace, username, and app password are all required.",
        )

    try:
        client = _get_client(config)
        try:
            resp = client.get("/user")
            resp.raise_for_status()
            user_data = resp.json()
            display_name = user_data.get("display_name", "unknown")
            return BitbucketValidationResult(
                ok=True,
                detail=f"Authenticated as {display_name}; workspace: {config.workspace}.",
            )
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return BitbucketValidationResult(ok=False, detail=f"Bitbucket connection failed: {err}")


def list_commits(
    config: BitbucketConfig,
    repo_slug: str,
    path: str = "",
    limit: int | None = None,
) -> dict[str, Any]:
    """List recent commits for a repository.

    Read-only: uses the commits endpoint.
    """
    if not config.is_configured:
        return {"source": "bitbucket", "available": False, "error": "Not configured."}

    effective_limit = min(limit or config.max_results, config.max_results)
    try:
        client = _get_client(config)
        try:
            url = f"/repositories/{config.workspace}/{repo_slug}/commits"
            params: dict[str, Any] = {"pagelen": effective_limit}
            if path:
                params["path"] = path
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            commits = []
            for entry in data.get("values", []):
                commits.append(
                    {
                        "hash": entry.get("hash", "")[:12],
                        "message": entry.get("message", "").split("\n")[0][:200],
                        "author": entry.get("author", {}).get("raw", ""),
                        "date": entry.get("date", ""),
                    }
                )
            return {
                "source": "bitbucket",
                "available": True,
                "repo": f"{config.workspace}/{repo_slug}",
                "total_returned": len(commits),
                "commits": commits,
            }
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "bitbucket", "available": False, "error": str(err)}


def get_file_contents(
    config: BitbucketConfig,
    repo_slug: str,
    path: str,
    ref: str = "",
) -> dict[str, Any]:
    """Retrieve file contents at a given path and revision.

    Read-only: uses the src endpoint.
    """
    if not config.is_configured:
        return {"source": "bitbucket", "available": False, "error": "Not configured."}

    try:
        client = _get_client(config)
        try:
            revision = ref or "HEAD"
            url = f"/repositories/{config.workspace}/{repo_slug}/src/{revision}/{path}"
            resp = client.get(url)
            resp.raise_for_status()
            content = resp.text[:10000]
            return {
                "source": "bitbucket",
                "available": True,
                "repo": f"{config.workspace}/{repo_slug}",
                "path": path,
                "ref": revision,
                "content": content,
                "truncated": len(resp.text) > 10000,
            }
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "bitbucket", "available": False, "error": str(err)}


def search_code(
    config: BitbucketConfig,
    query: str,
    repo_slug: str = "",
    limit: int | None = None,
) -> dict[str, Any]:
    """Search code across the workspace or a specific repository.

    Read-only: uses the code search endpoint.
    """
    if not config.is_configured:
        return {"source": "bitbucket", "available": False, "error": "Not configured."}

    effective_limit = min(limit or config.max_results, config.max_results)
    try:
        client = _get_client(config)
        try:
            if repo_slug:
                url = f"/repositories/{config.workspace}/{repo_slug}/search/code"
            else:
                url = f"/workspaces/{config.workspace}/search/code"
            resp = client.get(
                url,
                params={"search_query": query, "pagelen": effective_limit},
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for entry in data.get("values", []):
                file_info = entry.get("file", {})
                results.append(
                    {
                        "path": file_info.get("path", ""),
                        "repo": entry.get("repository", {}).get("full_name", ""),
                        "content_matches": len(entry.get("content_matches", [])),
                    }
                )
            return {
                "source": "bitbucket",
                "available": True,
                "query": query,
                "total_returned": len(results),
                "results": results,
            }
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "bitbucket", "available": False, "error": str(err)}

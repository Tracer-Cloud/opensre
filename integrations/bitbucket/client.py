"""Read-only Bitbucket Cloud REST client operations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from core.tool_framework.utils import tool_unavailable
from integrations._validation_helpers import report_validation_failure
from integrations.bitbucket.config import BitbucketConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BitbucketValidationResult:
    """Result of validating a Bitbucket integration."""

    ok: bool
    detail: str


def _get_client(config: BitbucketConfig) -> httpx.Client:
    """Create an authenticated httpx client for Bitbucket API calls."""
    return httpx.Client(
        base_url=config.base_url,
        auth=(config.username, config.app_password),
        timeout=config.timeout_seconds,
        headers={"Accept": "application/json"},
    )


def validate_bitbucket_config(config: BitbucketConfig) -> BitbucketValidationResult:
    """Validate Bitbucket connectivity by fetching the authenticated user."""
    if not config.is_configured:
        return BitbucketValidationResult(
            ok=False,
            detail="Bitbucket workspace, username, and app password are all required.",
        )

    try:
        client = _get_client(config)
        try:
            response = client.get("/user")
            response.raise_for_status()
            user_data = response.json()
            display_name = user_data.get("display_name", "unknown")
            return BitbucketValidationResult(
                ok=True,
                detail=f"Authenticated as {display_name}; workspace: {config.workspace}.",
            )
        finally:
            client.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="bitbucket",
            method="validate_bitbucket_config",
        )
        return BitbucketValidationResult(ok=False, detail=f"Bitbucket connection failed: {err}")


def list_commits(
    config: BitbucketConfig,
    repo_slug: str,
    path: str = "",
    limit: int | None = None,
) -> dict[str, Any]:
    """List recent commits for a repository."""
    if not config.is_configured:
        return tool_unavailable("bitbucket", "Not configured.")

    effective_limit = min(limit or config.max_results, config.max_results)
    try:
        client = _get_client(config)
        try:
            url = f"/repositories/{config.workspace}/{repo_slug}/commits"
            params: dict[str, Any] = {"pagelen": effective_limit}
            if path:
                params["path"] = path
            response = client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            commits = [
                {
                    "hash": entry.get("hash", "")[:12],
                    "message": entry.get("message", "").split("\n")[0][:200],
                    "author": entry.get("author", {}).get("raw", ""),
                    "date": entry.get("date", ""),
                }
                for entry in data.get("values", [])
            ]
            return {
                "source": "bitbucket",
                "available": True,
                "repo": f"{config.workspace}/{repo_slug}",
                "total_returned": len(commits),
                "effective_limit": effective_limit,
                "commits": commits,
            }
        finally:
            client.close()
    except Exception as err:
        report_validation_failure(
            err, logger=logger, integration="bitbucket", method="list_commits"
        )
        return tool_unavailable("bitbucket", str(err))


def get_file_contents(
    config: BitbucketConfig, repo_slug: str, path: str, ref: str = ""
) -> dict[str, Any]:
    """Retrieve file contents at a given path and revision."""
    if not config.is_configured:
        return tool_unavailable("bitbucket", "Not configured.")

    try:
        client = _get_client(config)
        try:
            revision = ref or "HEAD"
            url = f"/repositories/{config.workspace}/{repo_slug}/src/{revision}/{path}"
            response = client.get(url)
            response.raise_for_status()
            content = response.text[:10000]
            return {
                "source": "bitbucket",
                "available": True,
                "repo": f"{config.workspace}/{repo_slug}",
                "path": path,
                "ref": revision,
                "content": content,
                "truncated": len(response.text) > 10000,
            }
        finally:
            client.close()
    except Exception as err:
        report_validation_failure(
            err, logger=logger, integration="bitbucket", method="get_file_contents"
        )
        return tool_unavailable("bitbucket", str(err))


def search_code(
    config: BitbucketConfig,
    query: str,
    repo_slug: str = "",
    limit: int | None = None,
) -> dict[str, Any]:
    """Search code across the workspace or a specific repository."""
    if not config.is_configured:
        return tool_unavailable("bitbucket", "Not configured.")

    effective_limit = min(limit or config.max_results, config.max_results)
    try:
        client = _get_client(config)
        try:
            url = (
                f"/repositories/{config.workspace}/{repo_slug}/search/code"
                if repo_slug
                else f"/workspaces/{config.workspace}/search/code"
            )
            response = client.get(
                url,
                params={"search_query": query, "pagelen": effective_limit},
            )
            response.raise_for_status()
            data = response.json()
            results = [
                {
                    "path": entry.get("file", {}).get("path", ""),
                    "repo": entry.get("repository", {}).get("full_name", ""),
                    "content_matches": len(entry.get("content_matches", [])),
                }
                for entry in data.get("values", [])
            ]
            return {
                "source": "bitbucket",
                "available": True,
                "query": query,
                "total_returned": len(results),
                "effective_limit": effective_limit,
                "results": results,
            }
        finally:
            client.close()
    except Exception as err:
        report_validation_failure(err, logger=logger, integration="bitbucket", method="search_code")
        return tool_unavailable("bitbucket", str(err))

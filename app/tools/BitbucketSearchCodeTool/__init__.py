"""Bitbucket Search Code Tool."""

from __future__ import annotations

from typing import Any

from app.integrations.bitbucket import (
    BitbucketConfig,
    bitbucket_config_from_env,
    build_bitbucket_config,
    search_code,
)
from app.tools.tool_decorator import tool


def _resolve_config(
    workspace: str | None,
    username: str | None,
    app_password: str | None,
) -> BitbucketConfig | None:
    env_config = bitbucket_config_from_env()
    if any([workspace, username, app_password]):
        return build_bitbucket_config({
            "workspace": workspace or (env_config.workspace if env_config else ""),
            "username": username or (env_config.username if env_config else ""),
            "app_password": app_password or (env_config.app_password if env_config else ""),
        })
    return env_config


def _bb_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("bitbucket", {}).get("connection_verified"))


def _bb_creds(bb: dict[str, Any]) -> dict[str, Any]:
    return {
        "workspace": bb.get("workspace"),
        "username": bb.get("username"),
        "app_password": bb.get("app_password"),
    }


def _search_bitbucket_code_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    bb = sources["bitbucket"]
    return {
        "query": bb.get("query") or "exception OR error",
        "repo_slug": bb.get("repo_slug", ""),
        "limit": 20,
        **_bb_creds(bb),
    }


def _search_bitbucket_code_available(sources: dict[str, dict]) -> bool:
    return _bb_available(sources)


@tool(
    name="search_bitbucket_code",
    description="Search code across a Bitbucket workspace or specific repository.",
    source="bitbucket",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Finding where a specific function or configuration is defined",
        "Searching for error patterns across repositories",
    ],
    requires=["query"],
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "repo_slug": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 20},
            "workspace": {"type": "string"},
            "username": {"type": "string"},
            "app_password": {"type": "string"},
        },
        "required": ["query"],
    },
    is_available=_search_bitbucket_code_available,
    extract_params=_search_bitbucket_code_extract_params,
)
def search_bitbucket_code(
    query: str,
    workspace: str | None = None,
    username: str | None = None,
    app_password: str | None = None,
    repo_slug: str = "",
    limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Search code in a Bitbucket workspace."""
    config = _resolve_config(workspace, username, app_password)
    if config is None:
        return {"source": "bitbucket", "available": False, "error": "Bitbucket integration is not configured.", "results": []}
    return search_code(config, query=query, repo_slug=repo_slug, limit=limit)

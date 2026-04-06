"""Bitbucket Search Code Tool."""

from typing import Any

from app.integrations.bitbucket import BitbucketConfig, search_code
from app.tools.tool_decorator import tool


@tool(
    name="search_bitbucket_code",
    description="Search code across a Bitbucket workspace or specific repository.",
    source="bitbucket",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Finding where a specific function or configuration is defined",
        "Searching for error patterns across repositories",
    ],
)
def search_bitbucket_code(
    workspace: str,
    query: str,
    username: str,
    app_password: str,
    repo_slug: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Search code in a Bitbucket workspace."""
    config = BitbucketConfig(
        workspace=workspace,
        username=username,
        app_password=app_password,
    )
    return search_code(config, query=query, repo_slug=repo_slug, limit=limit)

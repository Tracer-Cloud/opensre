"""Bitbucket Commits Tool."""

from typing import Any

from app.integrations.bitbucket import BitbucketConfig, list_commits
from app.tools.tool_decorator import tool


@tool(
    name="list_bitbucket_commits",
    description="List recent commits for a Bitbucket repository, optionally filtered by file path.",
    source="bitbucket",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Checking whether a recent change could explain a failure",
        "Reviewing commit history for a specific file or directory",
    ],
)
def list_bitbucket_commits(
    workspace: str,
    repo_slug: str,
    username: str,
    app_password: str,
    path: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Fetch recent commits from a Bitbucket repository."""
    config = BitbucketConfig(
        workspace=workspace,
        username=username,
        app_password=app_password,
    )
    return list_commits(config, repo_slug=repo_slug, path=path, limit=limit)

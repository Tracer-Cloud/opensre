"""Bitbucket File Contents Tool."""

from typing import Any

from app.integrations.bitbucket import BitbucketConfig, get_file_contents
from app.tools.tool_decorator import tool


@tool(
    name="get_bitbucket_file_contents",
    description="Retrieve the contents of a file from a Bitbucket repository at a specific revision.",
    source="bitbucket",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Reading configuration files that may explain a failure",
        "Comparing file contents between revisions during investigation",
    ],
)
def get_bitbucket_file_contents(
    workspace: str,
    repo_slug: str,
    path: str,
    username: str,
    app_password: str,
    ref: str = "",
) -> dict[str, Any]:
    """Fetch file contents from a Bitbucket repository."""
    config = BitbucketConfig(
        workspace=workspace,
        username=username,
        app_password=app_password,
    )
    return get_file_contents(config, repo_slug=repo_slug, path=path, ref=ref)

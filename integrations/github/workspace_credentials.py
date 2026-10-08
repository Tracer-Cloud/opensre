"""Select GitHub credentials only for GitHub workspace transports."""

from integrations.git import GitCommandError, remote_transport_urls
from integrations.github.identity import workspace_public_repository_source


def github_workspace_git_token(workspace: str, token: str) -> str | None:
    """Return no token for other hosts, or None when GitHub needs an app connection."""
    github = [
        bool(workspace_public_repository_source({"workspace_repo": url}).get("github"))
        for url in remote_transport_urls(workspace)
    ]
    if not any(github):
        return ""
    if not token:
        return None
    if not all(github):
        raise GitCommandError(
            "github_transport_mismatch",
            "GitHub app authentication requires GitHub fetch and push destinations.",
        )
    return token

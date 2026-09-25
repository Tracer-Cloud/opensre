"""GitHub API defaults and environment variable names."""

from __future__ import annotations

GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_MCP_MODE_ENV = "GITHUB_MCP_MODE"
GITHUB_MCP_URL_ENV = "GITHUB_MCP_URL"
GITHUB_MCP_COMMAND_ENV = "GITHUB_MCP_COMMAND"
GITHUB_MCP_ARGS_ENV = "GITHUB_MCP_ARGS"
GITHUB_MCP_AUTH_TOKEN_ENV = "GITHUB_MCP_AUTH_TOKEN"
GITHUB_MCP_TOOLSETS_ENV = "GITHUB_MCP_TOOLSETS"
# Distinct ecosystem names: GitHub Actions injects GITHUB_TOKEN; the gh CLI reads GH_TOKEN.
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"
GH_TOKEN_ENV = "GH_TOKEN"
GITHUB_CLI_REQUIRED_SCOPES = frozenset({"read:org", "repo", "security_events", "workflow"})
GITHUB_CI_DEMO_REPOSITORY = "opensre-onboarding-ci-repair-demo"
#: What to check on a GitHub token that is refused, in the order that resolves it.
GITHUB_TOKEN_CHECKLIST = (
    "Check the GitHub token in this order (github.com/settings/personal-access-tokens). "
    "Fine-grained token: "
    "1. Expiration within the organization's maximum token lifetime; a longer one is refused "
    "for every organization repository and must be regenerated. "
    "2. Resource owner: the organization that owns the repository. "
    '3. Repository access: "Only select repositories" including this repository '
    '("Public repositories" is read-only). '
    '4. Permissions: Contents "Read and write" and Pull requests "Read and write", granted '
    "per selected repository. "
    "5. Update. A regenerated token must be reconnected on the Integrations page; "
    "edited permissions apply at once. "
    "Classic token: enable the repo scope (and workflow when CI files change) and, for an "
    "organization with SSO, authorize the token for that organization under Configure SSO."
)

__all__ = [
    "GITHUB_TOKEN_CHECKLIST",
    "GH_TOKEN_ENV",
    "GITHUB_API_BASE_URL",
    "GITHUB_CLI_REQUIRED_SCOPES",
    "GITHUB_CI_DEMO_REPOSITORY",
    "GITHUB_MCP_ARGS_ENV",
    "GITHUB_MCP_AUTH_TOKEN_ENV",
    "GITHUB_MCP_COMMAND_ENV",
    "GITHUB_MCP_MODE_ENV",
    "GITHUB_MCP_TOOLSETS_ENV",
    "GITHUB_MCP_URL_ENV",
    "GITHUB_TOKEN_ENV",
]

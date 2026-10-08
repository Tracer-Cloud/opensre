"""GitHub API defaults and environment variable names."""

from __future__ import annotations

GITHUB_CONNECTION_ORIGIN_TAG = "connection_origin"
GITHUB_WEBAPP_ORIGIN = "webapp"
GITHUB_LOCAL_ORIGIN = "local"
GITHUB_UNKNOWN_ORIGIN = "unknown"
GITHUB_CONNECTION_ORIGIN_PARAM = "github_connection_origin"
GITHUB_CONNECTION_ID_PARAM = "github_connection_id"
GITHUB_PROVENANCE_PARAM = "github_provenance"

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
#: Shell command that hands GitHub setup to the app.
GITHUB_INTEGRATION_SETUP_SLASH = "/integrations setup github"
#: Same app handoff from a terminal that is not already inside the interactive shell.
GITHUB_INTEGRATION_SETUP_CLI = "opensre integrations setup github"
#: ``slash_invoke`` accepts ``/integrations`` as the command and the rest as args.
GITHUB_SETUP_SLASH_INVOKE = 'slash_invoke(command="/integrations", args=["setup", "github"])'
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
    "GITHUB_CONNECTION_ORIGIN_TAG",
    "GITHUB_WEBAPP_ORIGIN",
    "GITHUB_LOCAL_ORIGIN",
    "GITHUB_UNKNOWN_ORIGIN",
    "GITHUB_CONNECTION_ORIGIN_PARAM",
    "GITHUB_CONNECTION_ID_PARAM",
    "GITHUB_PROVENANCE_PARAM",
    "GITHUB_TOKEN_CHECKLIST",
    "GH_TOKEN_ENV",
    "GITHUB_API_BASE_URL",
    "GITHUB_INTEGRATION_SETUP_CLI",
    "GITHUB_INTEGRATION_SETUP_SLASH",
    "GITHUB_SETUP_SLASH_INVOKE",
    "GITHUB_MCP_ARGS_ENV",
    "GITHUB_MCP_AUTH_TOKEN_ENV",
    "GITHUB_MCP_COMMAND_ENV",
    "GITHUB_MCP_MODE_ENV",
    "GITHUB_MCP_TOOLSETS_ENV",
    "GITHUB_MCP_URL_ENV",
    "GITHUB_TOKEN_ENV",
]

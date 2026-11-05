"""What the Gitee MCP server needs before it is considered configured.

Transport details (``mode``/``command``/``args``) are environment-only, like
the Sentry MCP setup: a user is prompted for the endpoint and the token, and
the model defaults already cover the remote Streamable HTTP case.
"""

from __future__ import annotations

from config.constants.gitee import GITEE_MCP_AUTH_TOKEN_ENV, GITEE_MCP_URL_ENV
from integrations.gitee import DEFAULT_GITEE_MCP_URL
from integrations.gitee.verifier import verify_gitee_mcp
from integrations.setup_flow import IntegrationSetupSpec, SetupField

URL_FIELD = "url"
AUTH_TOKEN_FIELD = "auth_token"

GITEE_MCP_SETUP = IntegrationSetupSpec(
    service="gitee_mcp",
    fields=(
        SetupField(
            name=URL_FIELD,
            label="Gitee MCP URL",
            env_var=GITEE_MCP_URL_ENV,
            default=DEFAULT_GITEE_MCP_URL,
        ),
        SetupField(
            name=AUTH_TOKEN_FIELD,
            label="Gitee personal access token",
            prompt="Gitee personal access token (repository read scope)",
            env_var=GITEE_MCP_AUTH_TOKEN_ENV,
            secret=True,
        ),
    ),
    verify=verify_gitee_mcp,
)

__all__ = [
    "AUTH_TOKEN_FIELD",
    "GITEE_MCP_SETUP",
    "URL_FIELD",
]

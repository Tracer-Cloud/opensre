"""One call for the login, token scopes, and who can create a demo repository."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel, report_run_error
from core.tool_framework import tool
from integrations.github.client import GitHubApiError, GitHubRestClient
from integrations.github.helpers import (
    GITHUB_INJECTED_PARAMS,
    github_creds,
    github_source_available,
)
from integrations.github.tools.ci_repair_loop.credentials import configured_token
from integrations.github.tools.repair_access.query import fetch_organization_nodes, owner_entries

TOKEN_CLASSIC_PAT = "classic PAT"
TOKEN_FINE_GRAINED_OR_APP = "fine-grained or app"
_OAUTH_SCOPES = "x-oauth-scopes"
_USER_PATH = "user"


def _credentials(sources: dict[str, dict]) -> dict[str, Any]:
    return github_creds(sources.get("github", {}))


def _login(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("login") or "").strip()


def _header(headers: Mapping[str, str], name: str) -> str | None:
    folded = name.casefold()
    for key, value in headers.items():
        if key.casefold() == folded:
            return value
    return None


def _token_access(headers: Mapping[str, str]) -> tuple[str, list[str]]:
    """Classic PAT when ``X-OAuth-Scopes`` lists scopes; otherwise fine-grained or app."""
    raw = _header(headers, _OAUTH_SCOPES)
    scopes = [] if raw is None else [part.strip() for part in raw.split(",") if part.strip()]
    if scopes:
        return TOKEN_CLASSIC_PAT, scopes
    return TOKEN_FINE_GRAINED_OR_APP, []


def _login_can_create(token_type: str, scopes: list[str]) -> bool:
    """Classic PATs create repositories only with ``repo`` or ``public_repo``.

    A fine-grained or app token omits ``X-OAuth-Scopes``, so this header
    cannot say whether that token can create a repository.
    """
    if token_type != TOKEN_CLASSIC_PAT:
        return True
    return "repo" in scopes or "public_repo" in scopes


def _response_text(
    login: str,
    token_type: str,
    scopes: list[str],
    owners: list[dict[str, Any]],
) -> str:
    scope_text = ", ".join(scopes) if scopes else "none"
    owner_text = " ".join(
        f"{item['login']} can create repositories."
        if item["can_create_repositories"]
        else f"{item['login']} cannot create repositories."
        for item in owners
    )
    return f"Login {login}; {token_type}; scopes {scope_text}. {owner_text}"


def _failed(exc: Exception) -> dict[str, Any]:
    report_run_error(
        exc,
        tool_name="probe_github_repair_access",
        source="github",
        component=__name__,
        method="probe_github_repair_access",
    )
    return {"ok": False, "error": f"Could not probe GitHub repair access: {type(exc).__name__}."}


@tool(
    name="probe_github_repair_access",
    source="github",
    display_name="Probe GitHub repair access",
    use_cases=["See which GitHub owners can host a private CI repair demo"],
    description=(
        "Read this token's GitHub login, whether it is a classic PAT (scopes from the "
        "X-OAuth-Scopes header) or a fine-grained or app token, and one entry per owner: "
        "the login, who can create repositories, plus each organization with its membership "
        "role, state, and viewerCanCreateRepositories. One REST user call and one GraphQL "
        "organizations query. Does not create a repository or choose a demo name."
    ),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    is_available=github_source_available,
    extract_params=_credentials,
    injected_params=GITHUB_INJECTED_PARAMS,
    input_schema={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
)
def probe_github_repair_access(
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Return login, token type, scopes, and which owners can create repositories."""
    try:
        client = GitHubRestClient(configured_token(github_token))
        user, headers = client.request_with_headers("GET", _USER_PATH)
        login = _login(user)
        if not login:
            return {"ok": False, "error": "GitHub did not return a login."}
        nodes = fetch_organization_nodes(client)
    except (GitHubApiError, OSError, RuntimeError, ValueError) as exc:
        return _failed(exc)
    token_type, scopes = _token_access(headers)
    owners = owner_entries(login, nodes, login_can_create=_login_can_create(token_type, scopes))
    return {
        "ok": True,
        "login": login,
        "token_type": token_type,
        "scopes": scopes,
        "owners": owners,
        "response_text": _response_text(login, token_type, scopes, owners),
    }

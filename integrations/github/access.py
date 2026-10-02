"""Recognize GitHub failures the user must fix by granting OpenSRE more access.

A GitHub error that only the account owner can resolve — an organization that
has not approved the OpenSRE OAuth App, SAML SSO that has not been authorized,
a token missing a scope, a revoked token, or a private repository outside the
grant — is turned into a :class:`GitHubAccessIssue`. Its ``user_action`` is the
sentence the agent relays: which page to open and what to click. Every other
failure classifies as ``None`` and keeps its original message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Literal, Protocol

from config.constants.account import OPENSRE_GITHUB_SETTINGS_PATH
from config.constants.github import GITHUB_INTEGRATION_SETUP_SLASH
from integrations.github.mcp_oauth import resolve_github_oauth_client_id

GitHubAccessIssueKind = Literal[
    "organization_approval",
    "sso_authorization",
    "missing_scopes",
    "reauthorize",
    "repository_access",
]


class HeaderLookup(Protocol):
    """Case-insensitive header access: ``http.client`` messages and httpx headers both fit."""

    def get(self, name: str, /) -> Any:
        """Return the header's value, or ``None`` when it is absent."""


#: ``action_required`` value on tool payloads that need the user to act.
GITHUB_PERMISSIONS_ACTION = "update_github_permissions"

_FINE_GRAINED_TOKENS_URL = "https://github.com/settings/personal-access-tokens"
_CLASSIC_TOKENS_URL = "https://github.com/settings/tokens"

_REPO_PATH_RE = re.compile(r"/repos/(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+)")
# A 404 here means the repository itself is invisible. A 404 on one file, PR, or
# commit usually just means that item does not exist.
_REPO_LEVEL_PATH_RE = re.compile(
    r"^/repos/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+"
    r"(/(pulls|issues|commits|branches|tags|releases|contents|readme|stargazers|languages"
    r"|contributors|actions/(runs|workflows)|code-scanning/alerts|dependabot/alerts))?/?$"
)
_ORG_PATH_RE = re.compile(r"/orgs/(?P<owner>[A-Za-z0-9._-]+)")
# GitHub MCP errors quote the REST call: "GET https://api.github.com/repos/o/r: 404 Not Found".
_MCP_HTTP_FAILURE_RE = re.compile(
    r"https://api\.github\.com(?P<path>/\S*?):?\s+(?P<status>[1-5]\d\d)\b"
)
# git push over HTTPS: "Permission to owner/repo.git denied to user."
_PUSH_DENIED_RE = re.compile(
    r"permission to (?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+?)(?:\.git)? denied",
    re.IGNORECASE,
)
# MCP errors that give a status without the API URL, e.g. "403 Forbidden".
_BARE_STATUS_RE = re.compile(
    r"\b(?P<status>401|403|404)\b\s+(?:Unauthorized|Forbidden|Not Found)", re.IGNORECASE
)
_SSO_URL_RE = re.compile(r"url=(?P<url>https://github\.com/\S+)")


@dataclass(frozen=True)
class GitHubAccessIssue:
    """A GitHub failure only the user can fix, and where they fix it."""

    kind: GitHubAccessIssueKind
    permissions_url: str
    user_action: str
    owner: str = ""
    repo: str = ""

    def as_payload(self) -> dict[str, str]:
        """Fields merged into a tool's unavailable payload for the agent."""
        payload = {
            "action_required": GITHUB_PERMISSIONS_ACTION,
            "access_issue": self.kind,
            "permissions_url": self.permissions_url,
            "user_action": self.user_action,
            "reconnect_command": GITHUB_INTEGRATION_SETUP_SLASH,
        }
        settings_url = github_settings_url()
        if settings_url:
            payload["settings_url"] = settings_url
        return payload


def github_settings_url() -> str:
    """The webapp's GitHub settings page for this install, or "" when unknown."""
    from config.account import webapp_base_url

    base = webapp_base_url()
    return f"{base}{OPENSRE_GITHUB_SETTINGS_PATH}" if base else ""


def github_oauth_app_permissions_url() -> str:
    """GitHub page where the user grants or requests organization access for OpenSRE."""
    return (
        f"https://github.com/settings/connections/applications/{resolve_github_oauth_client_id()}"
    )


def _workspace_permissions_url() -> str:
    """GitHub page the webapp named for this machine's workspace connection."""
    try:
        from integrations.store import get_integration

        integration = get_integration("github")
    except Exception:
        return ""
    instances = integration.get("instances") if isinstance(integration, dict) else None
    first = instances[0] if isinstance(instances, list) and instances else None
    tags = first.get("tags") if isinstance(first, dict) else None
    url = tags.get("permissions_url") if isinstance(tags, dict) else ""
    if isinstance(url, str) and url.startswith("https://github.com/"):
        return url
    return ""


def github_permissions_url(token: str = "") -> str:
    """Where the owner of ``token`` edits what it can reach."""
    if token.startswith("github_pat_"):
        return _FINE_GRAINED_TOKENS_URL
    if token.startswith("ghp_"):
        return _CLASSIC_TOKENS_URL
    return _workspace_permissions_url() or github_oauth_app_permissions_url()


def _reconnect_hint() -> str:
    settings_url = github_settings_url()
    if settings_url:
        return f"Reconnect at {settings_url} (or run `{GITHUB_INTEGRATION_SETUP_SLASH}` in the CLI)"
    return f"Run `{GITHUB_INTEGRATION_SETUP_SLASH}` to reconnect"


def _header(headers: HeaderLookup | None, name: str) -> str:
    if headers is None:
        return ""
    return str(headers.get(name) or "").strip()


def _scope_set(value: str) -> set[str]:
    return {part.strip() for part in value.split(",") if part.strip()}


def _owner_repo(path: str) -> tuple[str, str]:
    repo_match = _REPO_PATH_RE.search(path)
    if repo_match:
        return repo_match.group("owner"), repo_match.group("repo")
    org_match = _ORG_PATH_RE.search(path)
    return (org_match.group("owner"), "") if org_match else ("", "")


def _target(owner: str, repo: str) -> str:
    if owner and repo:
        return f"{owner}/{repo}"
    return owner or "this repository"


def classify_github_access_failure(
    *,
    status_code: int | None,
    message: str,
    headers: HeaderLookup | None = None,
    token: str = "",
    path: str = "",
) -> GitHubAccessIssue | None:
    """Return the access fix for a GitHub failure, or ``None`` when it is not one."""
    text = message or ""
    lowered = text.lower()
    if status_code is None or not path:
        mcp_match = _MCP_HTTP_FAILURE_RE.search(text)
        bare_match = _BARE_STATUS_RE.search(text)
        if mcp_match:
            path = path or mcp_match.group("path")
            status_code = status_code or int(mcp_match.group("status"))
        elif bare_match:
            status_code = status_code or int(bare_match.group("status"))
        if not path:
            repo_mention = _REPO_PATH_RE.search(text)
            path = repo_mention.group(0) if repo_mention else ""
    owner, repo = _owner_repo(path)
    target = _target(owner, repo)
    grant_url = github_permissions_url(token)

    if status_code == HTTPStatus.UNAUTHORIZED or "bad credentials" in lowered:
        return GitHubAccessIssue(
            kind="reauthorize",
            permissions_url=github_settings_url() or grant_url,
            user_action=(
                "GitHub rejected OpenSRE's token (it was revoked or expired). "
                f"Ask the user to reconnect GitHub. {_reconnect_hint()}, then retry."
            ),
            owner=owner,
            repo=repo,
        )

    sso_header = _header(headers, "X-GitHub-SSO")
    if sso_header.startswith("required") or "saml enforcement" in lowered:
        sso_match = _SSO_URL_RE.search(sso_header)
        sso_url = (
            sso_match.group("url")
            if sso_match
            else (f"https://github.com/orgs/{owner}/sso" if owner else grant_url)
        )
        return GitHubAccessIssue(
            kind="sso_authorization",
            permissions_url=sso_url,
            user_action=(
                f"The {owner or 'repository'} organization requires SAML single sign-on. "
                f"Ask the user to open {sso_url} and authorize OpenSRE's GitHub access "
                f"for {owner or 'that organization'}, then retry."
            ),
            owner=owner,
            repo=repo,
        )

    if "oauth app access restrictions" in lowered:
        oauth_url = _workspace_permissions_url() or github_oauth_app_permissions_url()
        return GitHubAccessIssue(
            kind="organization_approval",
            permissions_url=oauth_url,
            user_action=(
                f"The {owner or 'repository'} organization has not approved OpenSRE. "
                f"Ask the user to open {oauth_url}, find {owner or 'the organization'} under "
                "Organization access, and click Grant (or Request, if they are not an "
                "organization owner), then retry."
            ),
            owner=owner,
            repo=repo,
        )

    # git push: GitHub refuses workflow-file edits from a token without `workflow`.
    if "without `workflow` scope" in lowered or "without 'workflow' scope" in lowered:
        return GitHubAccessIssue(
            kind="missing_scopes",
            permissions_url=github_settings_url() or grant_url,
            user_action=(
                "GitHub refused the push because it changes a workflow file and OpenSRE's "
                "GitHub connection is missing the workflow permission. Ask the user to "
                f"reconnect GitHub and approve it. {_reconnect_hint()}, then retry."
            ),
            owner=owner,
            repo=repo,
        )

    push_denied = _PUSH_DENIED_RE.search(text)
    if push_denied:
        owner, repo = push_denied.group("owner"), push_denied.group("repo")
        return GitHubAccessIssue(
            kind="repository_access",
            permissions_url=grant_url,
            user_action=(
                f"GitHub refused the push to {owner}/{repo}: OpenSRE's GitHub access "
                f"cannot write there. Ask the user to grant OpenSRE access to {owner} at "
                f"{grant_url} (and confirm they can push to {owner}/{repo}), then retry."
            ),
            owner=owner,
            repo=repo,
        )

    # Fine-grained tokens send no X-OAuth-Scopes at all; only a scoped token can lack one.
    accepted = _scope_set(_header(headers, "X-Accepted-OAuth-Scopes"))
    granted = _scope_set(_header(headers, "X-OAuth-Scopes"))
    has_scope_header = headers is not None and headers.get("X-OAuth-Scopes") is not None
    if (
        status_code in (HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND)
        and has_scope_header
        and accepted
        and not (accepted & granted)
    ):
        scopes = " or ".join(sorted(accepted))
        return GitHubAccessIssue(
            kind="missing_scopes",
            permissions_url=github_settings_url() or grant_url,
            user_action=(
                f"OpenSRE's GitHub connection is missing the {scopes} permission. "
                f"Ask the user to reconnect GitHub and approve it. {_reconnect_hint()}, "
                "then retry."
            ),
            owner=owner,
            repo=repo,
        )

    repository_hidden = status_code == HTTPStatus.NOT_FOUND and bool(
        _REPO_LEVEL_PATH_RE.match(path.split("?", 1)[0])
    )
    if "resource not accessible by" in lowered or repository_hidden:
        return GitHubAccessIssue(
            kind="repository_access",
            permissions_url=grant_url,
            user_action=(
                f"OpenSRE's GitHub access does not cover {target} (GitHub reports private "
                "repositories it cannot read as not found). If it exists, ask the user to "
                f"grant OpenSRE access to {owner or 'its owner'} at {grant_url}, then retry."
            ),
            owner=owner,
            repo=repo,
        )
    return None


__all__ = [
    "GITHUB_PERMISSIONS_ACTION",
    "GitHubAccessIssue",
    "GitHubAccessIssueKind",
    "HeaderLookup",
    "classify_github_access_failure",
    "github_oauth_app_permissions_url",
    "github_permissions_url",
    "github_settings_url",
]

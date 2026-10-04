"""The skill prerequisite gate and the CI analyzer agree on whether GitHub is ready.

The carried-over gate asked the OpenSRE app whether the organization had an
active GitHub record, while the analyzer asked whether a token resolved. An app
record without a token passed the gate and then failed at the analysis step;
a local or environment token was refused by the gate although the analyzer
would have run. Both now share one predicate on the session's chosen GitHub
grant, so they cannot disagree — including when that choice is invalid.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from rich.console import Console

import integrations.account_integrations as acct
import integrations.github.tools.ci_analytics.analysis as analysis
import integrations.github.tools.ci_analytics.tool as analyzer
from config.account import AccountRecord
from config.constants import (
    GH_TOKEN_ENV,
    GITHUB_MCP_AUTH_TOKEN_ENV,
    GITHUB_MCP_COMMAND_ENV,
    GITHUB_MCP_MODE_ENV,
    GITHUB_MCP_URL_ENV,
    GITHUB_TOKEN_ENV,
    INTEGRATIONS_STORE_PATH_ENV,
    ORGANIZATION_ID_ENV,
    WEBAPP_URL_ENV,
)
from config.constants.skills import ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME
from core.agent_harness.spi.integrations import resolve_and_cache_integrations
from core.agent_harness.tools import ActionToolScope
from core.agent_harness.tools.action_tools import get_action_tools_from_integrations_view
from core.llm.types import ToolCall
from core.tool.execution import execute_tool_calls
from integrations.github.tools.ci_analytics.collector import CollectedRuns
from integrations.store import upsert_integration
from surfaces.interactive_shell.session import Session
from tools.interactive_shell.actions.skill_prerequisite_gate import gate_skill_entry

_EMPTY_HISTORY = CollectedRuns(
    default_branch="main", branch_runs=[], pr_runs=[], merged_prs=(), coverage_notices=[]
)
_FIRST = {"auth_token": "first-tok"}
_SECOND = {"auth_token": "second-tok"}


def _app_grants(monkeypatch: pytest.MonkeyPatch, grants: dict[str, dict[str, str]]) -> None:
    """Sign in; the OpenSRE app returns one GitHub connection per ``grants`` entry."""
    record = AccountRecord(
        user_id="user-1",
        organization_id="org-1",
        email=None,
        app_url="https://app.test",
        signed_in_at="2026-01-01T00:00:00Z",
        token_expires_at="2027-01-01T00:00:00Z",
    )
    payload = {
        "success": True,
        "data": [
            {
                "id": grant_id,
                "service": "github",
                "status": "active",
                "name": "default",
                "credentials": credentials,
            }
            for grant_id, credentials in grants.items()
        ],
    }

    def load_record() -> AccountRecord:
        return record

    def account_token() -> str:
        return "osre_pat_secret_value"

    def get(_url: str, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(acct, "load_account_record", load_record)
    monkeypatch.setattr(acct, "resolve_account_token", account_token)
    monkeypatch.setattr(acct, "httpx", SimpleNamespace(get=get, HTTPError=httpx.HTTPError))


def _account_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _app_grants(monkeypatch, {"gh-app": {"auth_token": "app-tok"}})


def _account_record_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _app_grants(monkeypatch, {"gh-app": {}})


def _local_store_token(_monkeypatch: pytest.MonkeyPatch) -> None:
    upsert_integration("github", {"credentials": {"auth_token": "local-tok"}})


def _gh_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GH_TOKEN_ENV, "env-tok")


def _nothing(_monkeypatch: pytest.MonkeyPatch) -> None:
    return None


def _two_grants_and_an_env_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """An env token stays in reach, so an invalid choice must not fall back to it."""
    _app_grants(monkeypatch, {"gh-first": _FIRST, "gh-second": _SECOND, "gh-unusable": {}})
    monkeypatch.setenv(GH_TOKEN_ENV, "env-tok")


@pytest.mark.parametrize(
    ("arrange", "connection_id", "token"),
    [
        (_account_token, None, "app-tok"),
        (_account_record_without_token, None, None),
        (_local_store_token, None, "local-tok"),
        (_gh_token_env, None, "env-tok"),
        (_nothing, None, None),
        (_two_grants_and_an_env_token, "gh-unknown", None),
        (_two_grants_and_an_env_token, "gh-unusable", None),
        (_two_grants_and_an_env_token, "gh-second", "second-tok"),
    ],
    ids=[
        "app-token",
        "app-record-without-token",
        "local-store",
        "gh-token-env",
        "nothing",
        "selected-grant-missing",
        "selected-grant-unusable",
        "second-grant-selected",
    ],
)
def test_the_gate_passes_exactly_when_the_analyzer_runs_with_the_chosen_grant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    arrange: Callable[[pytest.MonkeyPatch], None],
    connection_id: str | None,
    token: str | None,
) -> None:
    # Arrange: no ambient credentials, then the scenario's sources. An MCP URL or
    # command from the repo ``.env`` alone yields a tokenless ``github`` integration.
    for name in (
        GITHUB_TOKEN_ENV,
        GH_TOKEN_ENV,
        GITHUB_MCP_AUTH_TOKEN_ENV,
        GITHUB_MCP_URL_ENV,
        GITHUB_MCP_MODE_ENV,
        GITHUB_MCP_COMMAND_ENV,
        "JWT_TOKEN",
        WEBAPP_URL_ENV,
        ORGANIZATION_ID_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(INTEGRATIONS_STORE_PATH_ENV, str(tmp_path / "integrations.json"))
    arrange(monkeypatch)
    clients: list[str | None] = []

    def client(github_token: str | None = None) -> object:
        clients.append(github_token)
        return object()

    def collect(_client: object, **_kwargs: Any) -> CollectedRuns:
        return _EMPTY_HISTORY

    monkeypatch.setattr(analysis, "GitHubRestClient", client)
    monkeypatch.setattr(analysis, "collect_runs", collect)
    monkeypatch.setattr(analyzer, "snapshot_root", lambda _root=None: tmp_path)
    # The turn's view: the session's integrations with its chosen grant applied.
    session = Session()
    session.integrations.github_connection_id = connection_id
    resolved = resolve_and_cache_integrations(session)
    scope = ActionToolScope(session=session, console=Console(file=io.StringIO()), is_tty=False)

    # Act: the gate on the skill; the analyzer as a turn lists and runs it.
    held = gate_skill_entry(
        ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME, scope, resolved_integrations=resolved
    )
    listed = {
        tool.name: tool
        for tool in get_action_tools_from_integrations_view(scope, resolved_integrations=resolved)
    }
    tool = listed.get(analyzer.TOOL_NAME)
    analyzed = False
    if tool is not None:
        [result] = execute_tool_calls(
            [ToolCall(id="call-1", name=tool.name, input={"owner": "acme", "repo": "w"})],
            [tool],
            resolved,
        )
        analyzed = isinstance(result.details, dict) and result.details.get("success") is True

    # Assert: same verdict, and the analyzer read GitHub with the chosen grant only.
    assert (held is None) is analyzed
    assert analyzed is (token is not None)
    assert clients == ([token] if token is not None else [])

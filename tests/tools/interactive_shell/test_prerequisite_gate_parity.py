"""The skill prerequisite gate and the CI analyzer agree on whether GitHub is ready.

The carried-over gate asked the OpenSRE app whether the organization had an
active GitHub record, while the analyzer asked whether a token resolved. An app
record without a token passed the gate and then failed at the analysis step;
a local or environment token was refused by the gate although the analyzer
would have run. Both now share one predicate, so they cannot disagree.
"""

from __future__ import annotations

import io
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
    GITHUB_TOKEN_ENV,
    INTEGRATIONS_STORE_PATH_ENV,
    ORGANIZATION_ID_ENV,
    WEBAPP_URL_ENV,
)
from config.constants.skills import ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME
from core.agent_harness.tools import ActionToolScope
from core.llm.types import ToolCall
from core.tool import REGISTERED_TOOL_ATTR, RegisteredTool
from core.tool.execution import execute_tool_calls
from infrastructure.harness_providers import resolve_integrations
from integrations.github.tools.ci_analytics.collector import CollectedRuns
from integrations.store import upsert_integration
from surfaces.interactive_shell.session import Session
from tools.interactive_shell.actions.skill_prerequisite_gate import gate_skill_entry

_EMPTY_HISTORY = CollectedRuns(
    default_branch="main", branch_runs=[], pr_runs=[], merged_prs=(), coverage_notices=[]
)


def _sign_in_with_app_github(monkeypatch: pytest.MonkeyPatch, credentials: dict[str, str]) -> None:
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
                "id": "github-org-1",
                "service": "github",
                "status": "active",
                "name": "default",
                "credentials": credentials,
            }
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
    _sign_in_with_app_github(monkeypatch, {"auth_token": "app-tok"})


def _account_record_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _sign_in_with_app_github(monkeypatch, {})


def _local_store_token(_monkeypatch: pytest.MonkeyPatch) -> None:
    upsert_integration("github", {"credentials": {"auth_token": "local-tok"}})


def _gh_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GH_TOKEN_ENV, "env-tok")


def _nothing(_monkeypatch: pytest.MonkeyPatch) -> None:
    return None


@pytest.mark.parametrize(
    ("arrange", "token"),
    [
        (_account_token, "app-tok"),
        (_account_record_without_token, None),
        (_local_store_token, "local-tok"),
        (_gh_token_env, "env-tok"),
        (_nothing, None),
    ],
    ids=["app-token", "app-record-without-token", "local-store", "gh-token-env", "nothing"],
)
def test_the_gate_passes_exactly_when_the_analyzer_finds_a_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    arrange: Any,
    token: str | None,
) -> None:
    # Arrange: no ambient credentials, then exactly one source (or none).
    for name in (
        GITHUB_TOKEN_ENV,
        GH_TOKEN_ENV,
        GITHUB_MCP_AUTH_TOKEN_ENV,
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
    resolved = resolve_integrations()
    scope = ActionToolScope(session=Session(), console=Console(file=io.StringIO()), is_tty=False)
    tool = getattr(analyzer.analyze_github_ci_reliability, REGISTERED_TOOL_ATTR)
    assert isinstance(tool, RegisteredTool)

    # Act: the gate on the skill, and the analyzer through the real tool runtime.
    held = gate_skill_entry(
        ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME, scope, resolved_integrations=resolved
    )
    [result] = execute_tool_calls(
        [ToolCall(id="call-1", name=analyzer.TOOL_NAME, input={"owner": "acme", "repo": "w"})],
        [tool],
        resolved,
    )

    # Assert
    analyzed = isinstance(result.details, dict) and result.details.get("success") is True
    assert (held is None) is analyzed
    assert analyzed is (token is not None)
    assert clients == ([token] if token is not None else [])

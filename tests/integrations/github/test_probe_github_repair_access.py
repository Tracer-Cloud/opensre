"""The repair-access probe is one user read and one organizations query."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.github.tools.repair_access import tool as repair_access


class _Client:
    """Records REST and GraphQL calls and returns two organizations at once."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request_with_headers(
        self,
        method: str,
        path: str,
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        self.calls.append((method, path, None))
        return {"login": "octocat", "id": 1}, self.headers

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append((method, path, body))
        return {
            "data": {
                "viewer": {
                    "login": "octocat",
                    "organizations": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "login": "acme",
                                "viewerCanAdminister": True,
                                "viewerCanCreateRepositories": True,
                                "viewerIsAMember": True,
                            },
                            {
                                "login": "other",
                                "viewerCanAdminister": False,
                                "viewerCanCreateRepositories": False,
                                "viewerIsAMember": True,
                            },
                        ],
                    },
                }
            }
        }


def _token(_explicit: str | None) -> str:
    return "token"


def _install(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    def _factory(_token_value: str) -> _Client:
        return client

    monkeypatch.setattr(repair_access, "configured_token", _token)
    monkeypatch.setattr(repair_access, "GitHubRestClient", _factory)


@pytest.mark.parametrize(
    ("headers", "token_type", "scopes"),
    [
        ({"X-OAuth-Scopes": "repo, workflow"}, "classic PAT", ["repo", "workflow"]),
        ({}, "fine-grained or app", []),
    ],
)
def test_probe_uses_one_organizations_query_and_reports_can_create(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    token_type: str,
    scopes: list[str],
) -> None:
    client = _Client(headers)
    _install(monkeypatch, client)

    result = repair_access.probe_github_repair_access(
        github_connection_origin="webapp",
        github_token="app-token",
    )

    user_reads = [call for call in client.calls if call[0] == "GET" and call[1] == "user"]
    graphql = [call for call in client.calls if call[0] == "POST"]
    assert len(user_reads) == 1
    assert len(graphql) == 1
    body = graphql[0][2]
    assert body is not None
    query = str(body["query"])
    assert "organizations(" in query
    assert "viewerCanCreateRepositories" in query
    assert "organization(login:" not in query
    assert result["ok"] is True
    assert result["login"] == "octocat"
    assert result["token_type"] == token_type
    assert result["scopes"] == scopes
    owners = result["owners"]
    assert [item["login"] for item in owners] == ["octocat", "acme", "other"]
    assert owners[0]["can_create_repositories"] is True
    assert owners[1]["can_create_repositories"] is True
    assert owners[2]["can_create_repositories"] is False
    assert "opensre-ci-repair-demo" not in result["response_text"]


def test_a_classic_pat_without_repo_scope_cannot_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client({"X-OAuth-Scopes": "read:org"})
    _install(monkeypatch, client)

    result = repair_access.probe_github_repair_access(
        github_connection_origin="webapp",
        github_token="app-token",
    )

    assert result["owners"][0]["can_create_repositories"] is False

"""Request contract for Bitbucket code search."""

from __future__ import annotations

from http import HTTPStatus

import httpx
import pytest

from integrations.bitbucket import client as bitbucket_client
from integrations.bitbucket.client import search_code
from integrations.bitbucket.config import BitbucketConfig

# Trimmed from a live GET /workspaces/{workspace}/search/code response with the
# repository requested through `fields`: the repository sits under file.commit.
_SEARCH_RESPONSE = {
    "size": 1,
    "page": 1,
    "pagelen": 20,
    "query_substituted": False,
    "values": [
        {
            "type": "code_search_result",
            "content_match_count": 1,
            "content_matches": [{"lines": [{"line": 3, "segments": []}]}],
            "path_matches": [],
            "file": {
                "path": "src/payments/retry.py",
                "type": "commit_file",
                "commit": {
                    "type": "commit",
                    "repository": {"type": "repository", "full_name": "acme/payments-api"},
                },
            },
        }
    ],
}


@pytest.mark.parametrize(
    ("repo_slug", "expected_search_query"),
    [
        ("payments-api", "TimeoutError repo:payments-api"),
        ("", "TimeoutError"),
    ],
)
def test_search_code_uses_workspace_endpoint_and_reports_repository(
    monkeypatch: pytest.MonkeyPatch, repo_slug: str, expected_search_query: str
) -> None:
    """Repo scoping must use the `repo:` qualifier, since the repo-level path 404s."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(HTTPStatus.OK, json=_SEARCH_RESPONSE, request=request)

    def fake_get_client(config: BitbucketConfig) -> httpx.Client:
        return httpx.Client(base_url=config.base_url, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(bitbucket_client, "_get_client", fake_get_client)
    config = BitbucketConfig(workspace="acme", username="bb-user", app_password="bb-pass")

    result = search_code(config, query="TimeoutError", repo_slug=repo_slug, limit=20)

    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/2.0/workspaces/acme/search/code"
    assert dict(requests[0].url.params) == {
        "search_query": expected_search_query,
        "pagelen": "20",
        "fields": "+values.file.commit.repository.full_name",
    }
    assert result["available"] is True
    assert result["results"] == [
        {"path": "src/payments/retry.py", "repo": "acme/payments-api", "content_matches": 1}
    ]

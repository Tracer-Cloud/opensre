from __future__ import annotations

from http import HTTPStatus
from unittest.mock import patch

import httpx
import pytest

from integrations.github.runbooks.revisions import (
    GitHubRevisionComparisonError,
    is_github_revision_reachable,
)


@pytest.mark.parametrize(
    ("status", "expected"),
    (("identical", True), ("behind", True), ("ahead", False), ("diverged", False)),
)
def test_revision_reachability_uses_comparison_status(status: str, expected: bool) -> None:
    response = httpx.Response(
        HTTPStatus.OK,
        json={"status": status},
        request=httpx.Request("GET", "https://api.github.com"),
    )

    with patch("integrations.github.runbooks.revisions.httpx.get", return_value=response):
        reachable = is_github_revision_reachable(
            owner="acme",
            repo="operations",
            trusted_revision="b" * 40,
            candidate_revision="a" * 40,
            auth_token="secret",
        )

    assert reachable is expected


def test_revision_reachability_fails_closed_without_authentication() -> None:
    with pytest.raises(GitHubRevisionComparisonError, match="authentication is unavailable"):
        is_github_revision_reachable(
            owner="acme",
            repo="operations",
            trusted_revision="b" * 40,
            candidate_revision="a" * 40,
            auth_token="",
        )


def test_revision_reachability_fails_closed_when_comparison_is_rejected() -> None:
    response = httpx.Response(
        HTTPStatus.NOT_FOUND,
        request=httpx.Request("GET", "https://api.github.com"),
    )

    with (
        patch("integrations.github.runbooks.revisions.httpx.get", return_value=response),
        pytest.raises(GitHubRevisionComparisonError, match="comparison request was rejected"),
    ):
        is_github_revision_reachable(
            owner="acme",
            repo="operations",
            trusted_revision="b" * 40,
            candidate_revision="a" * 40,
            auth_token="secret",
        )

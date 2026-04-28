"""Tests for BitbucketCommitsTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import patch

import pytest

from app.tools.BitbucketCommitsTool import list_bitbucket_commits
from tests.tools.conftest import BaseToolContract


def _registered_tool() -> Any:
    return cast(Any, list_bitbucket_commits).__opensre_registered_tool__


class TestBitbucketCommitsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool()


@pytest.mark.parametrize(
    "sources,expected",
    [
        (
            {
                "bitbucket": {
                    "connection_verified": True,
                    "workspace": "acme",
                    "username": "bb-user",
                    "app_password": "bb-pass",
                    "repo_slug": "backend-service",
                }
            },
            True,
        ),
        ({"bitbucket": {"connection_verified": True, "workspace": "acme"}}, False),
        ({"bitbucket": {"connection_verified": True, "username": "bb-user"}}, False),
        ({"bitbucket": {"connection_verified": True, "app_password": "bb-pass"}}, False),
        (
            {"bitbucket": {"workspace": "acme", "username": "bb-user", "app_password": "bb-pass"}},
            False,
        ),
        (
            {
                "bitbucket": {
                    "connection_verified": True,
                    "workspace": "acme",
                    "username": "bb-user",
                    "app_password": "bb-pass",
                }
            },
            False,
        ),
        ({}, False),
    ],
)
def test_is_available_requires_repo_and_credentials(sources: dict, expected: bool) -> None:
    rt = _registered_tool()
    assert rt.is_available(sources) is expected


def test_extract_params_maps_fields() -> None:
    rt = _registered_tool()
    params = rt.extract_params(
        {
            "bitbucket": {
                "repo_slug": "backend-service",
                "path": "src/main.py",
                "workspace": "acme",
                "username": "bb-user",
                "app_password": "bb-pass",
            }
        }
    )

    assert params["repo_slug"] == "backend-service"
    assert params["path"] == "src/main.py"
    assert params["workspace"] == "acme"
    assert params["username"] == "bb-user"
    assert params["app_password"] == "bb-pass"
    assert params["limit"] == 20


def test_run_happy_path() -> None:
    mock_result: dict[str, Any] = {
        "source": "bitbucket",
        "available": True,
        "repo": "acme/backend-service",
        "total_returned": 1,
        "commits": [
            {
                "hash": "abc123def456",
                "message": "Fix flaky test",
                "author": "Jane Doe",
                "date": "2026-04-28T10:00:00Z",
            }
        ],
    }

    with patch(
        "app.tools.BitbucketCommitsTool.list_commits", return_value=mock_result
    ) as mocked_list_commits:
        result = list_bitbucket_commits(
            repo_slug="backend-service",
            workspace="acme",
            username="bb-user",
            app_password="bb-pass",
            path="src/main.py",
            limit=5,
        )

    assert result == mock_result
    mocked_list_commits.assert_called_once()
    config = mocked_list_commits.call_args.args[0]
    assert config.workspace == "acme"
    assert config.username == "bb-user"
    assert config.app_password == "bb-pass"
    assert mocked_list_commits.call_args.kwargs == {
        "repo_slug": "backend-service",
        "path": "src/main.py",
        "limit": 5,
    }


def test_run_returns_unavailable_without_credentials() -> None:
    # Ensure env-based config doesn't make this test flaky
    with patch("app.tools.BitbucketSearchCodeTool.bitbucket_config_from_env", return_value=None):
        result = list_bitbucket_commits(repo_slug="backend-service")

    assert result["available"] is False
    assert result["commits"] == []
    assert result["error"] == "Bitbucket integration is not configured."

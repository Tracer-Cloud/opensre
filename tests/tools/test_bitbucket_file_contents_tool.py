"""Tests for BitbucketFileContentsTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import patch

import pytest

from app.tools.BitbucketFileContentsTool import get_bitbucket_file_contents
from tests.tools.conftest import BaseToolContract


def _registered_tool() -> Any:
    return cast(Any, get_bitbucket_file_contents).__opensre_registered_tool__


class TestBitbucketFileContentsToolContract(BaseToolContract):
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
                    "path": "src/main.py",
                }
            },
            True,
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
        (
            {
                "bitbucket": {
                    "connection_verified": True,
                    "workspace": "acme",
                    "username": "bb-user",
                    "app_password": "bb-pass",
                    "path": "src/main.py",
                }
            },
            False,
        ),
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
            False,
        ),
        ({"bitbucket": {"connection_verified": True, "workspace": "acme"}}, False),
        ({"bitbucket": {"connection_verified": True, "repo_slug": "backend-service"}}, False),
        ({"bitbucket": {"connection_verified": True, "path": "src/main.py"}}, False),
        ({}, False),
    ],
)
def test_is_available_requires_file_and_credentials(sources: dict, expected: bool) -> None:
    rt = _registered_tool()
    assert rt.is_available(sources) is expected


def test_extract_params_maps_fields() -> None:
    rt = _registered_tool()
    params = rt.extract_params(
        {
            "bitbucket": {
                "repo_slug": "backend-service",
                "path": "src/main.py",
                "ref": "main",
                "workspace": "acme",
                "username": "bb-user",
                "app_password": "bb-pass",
            }
        }
    )

    assert params["repo_slug"] == "backend-service"
    assert params["path"] == "src/main.py"
    assert params["ref"] == "main"
    assert params["workspace"] == "acme"
    assert params["username"] == "bb-user"
    assert params["app_password"] == "bb-pass"


def test_run_happy_path() -> None:
    mock_result: dict[str, Any] = {
        "source": "bitbucket",
        "available": True,
        "repo": "acme/backend-service",
        "path": "src/main.py",
        "ref": "main",
        "content": "print('hello')",
        "truncated": False,
    }

    with patch(
        "app.tools.BitbucketFileContentsTool.get_file_contents",
        return_value=mock_result,
    ) as mocked_get_file_contents:
        result = get_bitbucket_file_contents(
            repo_slug="backend-service",
            path="src/main.py",
            workspace="acme",
            username="bb-user",
            app_password="bb-pass",
            ref="main",
        )

    assert result == mock_result
    mocked_get_file_contents.assert_called_once()
    config = mocked_get_file_contents.call_args.args[0]
    assert config.workspace == "acme"
    assert config.username == "bb-user"
    assert config.app_password == "bb-pass"
    assert mocked_get_file_contents.call_args.kwargs == {
        "repo_slug": "backend-service",
        "path": "src/main.py",
        "ref": "main",
    }


def test_run_returns_unavailable_without_credentials() -> None:
    # Prevent loading real env config in CI/local runs
    with patch("app.tools.BitbucketSearchCodeTool.bitbucket_config_from_env", return_value=None):
        result = get_bitbucket_file_contents(repo_slug="backend-service", path="src/main.py")

    assert result["available"] is False
    assert result["file"] == {}
    assert result["error"] == "Bitbucket integration is not configured."

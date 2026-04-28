"""Tests for BitbucketSearchCodeTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from app.tools.BitbucketSearchCodeTool import search_bitbucket_code
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestBitbucketSearchCodeToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return search_bitbucket_code.__opensre_registered_tool__


def test_is_available_requires_connection_verified() -> None:
    rt = search_bitbucket_code.__opensre_registered_tool__
    assert rt.is_available({"bitbucket": {"connection_verified": True}}) is True
    assert rt.is_available({"bitbucket": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = search_bitbucket_code.__opensre_registered_tool__
    sources = mock_agent_state(
        {
            "bitbucket": {
                "connection_verified": True,
                "query": "panic",
                "repo_slug": "backend-service",
                "workspace": "acme",
                "username": "bb-user",
                "app_password": "bb-pass",
                "base_url": "https://api.bitbucket.org/2.0",
                "max_results": 50,
                "integration_id": "bb-main",
            }
        }
    )
    params = rt.extract_params(sources)
    assert params["query"] == "panic"
    assert params["repo_slug"] == "backend-service"
    assert params["workspace"] == "acme"
    assert params["username"] == "bb-user"
    assert params["app_password"] == "bb-pass"
    assert params["base_url"] == "https://api.bitbucket.org/2.0"
    assert params["max_results"] == 50
    assert params["integration_id"] == "bb-main"


def test_run_returns_unavailable_when_no_config() -> None:
    with patch("app.tools.BitbucketSearchCodeTool.bitbucket_config_from_env", return_value=None):
        result = search_bitbucket_code(query="error OR exception")

    assert result == {
        "source": "bitbucket",
        "available": False,
        "error": "Bitbucket integration is not configured.",
        "results": [],
    }


def test_run_happy_path() -> None:
    mock_result: dict[str, Any] = {
        "source": "bitbucket",
        "available": True,
        "query": "panic",
        "results": [{"file_path": "src/main.py", "line": 13, "snippet": "panic(err)"}],
    }

    with (
        patch("app.tools.BitbucketSearchCodeTool.bitbucket_config_from_env", return_value=None),
        patch("app.tools.BitbucketSearchCodeTool.build_bitbucket_config", return_value=object()),
        patch("app.tools.BitbucketSearchCodeTool.search_code", return_value=mock_result),
    ):
        result = search_bitbucket_code(
            query="panic",
            workspace="acme",
            username="bb-user",
            app_password="bb-pass",
        )

    assert result == mock_result

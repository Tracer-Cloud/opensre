"""Unit tests for BitbucketSearchCodeTool (search_bitbucket_code)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.BitbucketSearchCodeTool import search_bitbucket_code
from tests.tools.conftest import BaseToolContract


class TestBitbucketSearchCodeToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return search_bitbucket_code.__opensre_registered_tool__


# ── is_available ────────────────────────────────────────────────────

def test_is_available_true_with_full_creds() -> None:
    rt = search_bitbucket_code.__opensre_registered_tool__
    sources = {
        "bitbucket": {
            "workspace": "my-workspace",
            "username": "user",
            "app_password": "secret",
        }
    }
    assert rt.is_available(sources) is True


def test_is_available_false_missing_creds() -> None:
    rt = search_bitbucket_code.__opensre_registered_tool__
    assert rt.is_available({"bitbucket": {}}) is False


def test_is_available_false_missing_bitbucket_key() -> None:
    rt = search_bitbucket_code.__opensre_registered_tool__
    assert rt.is_available({}) is False


# ── extract_params ──────────────────────────────────────────────────

def test_extract_params_maps_fields() -> None:
    rt = search_bitbucket_code.__opensre_registered_tool__
    sources = {
        "bitbucket": {
            "workspace": "my-workspace",
            "username": "user",
            "app_password": "secret",
            "query": "def connect",
        }
    }
    params = rt.extract_params(sources)
    assert params["workspace"] == "my-workspace"
    assert params["username"] == "user"
    assert params["app_password"] == "secret"


# ── run ─────────────────────────────────────────────────────────────

def test_run_returns_results_on_success() -> None:
    mock_results = {
        "values": [
            {"file": {"path": "app/db.py"}, "lines": [{"line": "def connect():"}]},
        ]
    }
    with patch("app.tools.BitbucketSearchCodeTool.search_code", return_value=mock_results):
        result = search_bitbucket_code(
            query="def connect",
            workspace="my-workspace",
            username="user",
            app_password="secret",
        )
    assert result["available"] is True
    assert len(result["results"]) >= 1


def test_run_returns_unavailable_on_exception() -> None:
    with patch(
        "app.tools.BitbucketSearchCodeTool.search_code",
        side_effect=Exception("auth error"),
    ):
        result = search_bitbucket_code(
            query="def connect",
            workspace="my-workspace",
            username="user",
            app_password="secret",
        )
    assert result["available"] is False
    assert "error" in result

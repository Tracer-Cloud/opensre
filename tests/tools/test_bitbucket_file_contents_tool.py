"""Unit tests for BitbucketFileContentsTool (get_bitbucket_file_contents)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.BitbucketFileContentsTool import get_bitbucket_file_contents
from tests.tools.conftest import BaseToolContract


class TestBitbucketFileContentsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_bitbucket_file_contents.__opensre_registered_tool__


# ── is_available ────────────────────────────────────────────────────

def test_is_available_true_with_full_creds() -> None:
    rt = get_bitbucket_file_contents.__opensre_registered_tool__
    sources = {
        "bitbucket": {
            "workspace": "my-workspace",
            "username": "user",
            "app_password": "secret",
            "repo_slug": "my-repo",
            "path": "config/settings.yaml",
        }
    }
    assert rt.is_available(sources) is True


def test_is_available_false_missing_path() -> None:
    rt = get_bitbucket_file_contents.__opensre_registered_tool__
    sources = {
        "bitbucket": {
            "workspace": "my-workspace",
            "username": "user",
            "app_password": "secret",
            "repo_slug": "my-repo",
        }
    }
    assert rt.is_available(sources) is False


def test_is_available_false_missing_bitbucket_key() -> None:
    rt = get_bitbucket_file_contents.__opensre_registered_tool__
    assert rt.is_available({}) is False


# ── extract_params ──────────────────────────────────────────────────

def test_extract_params_maps_fields() -> None:
    rt = get_bitbucket_file_contents.__opensre_registered_tool__
    sources = {
        "bitbucket": {
            "workspace": "my-workspace",
            "username": "user",
            "app_password": "secret",
            "repo_slug": "my-repo",
            "path": "config/settings.yaml",
            "ref": "main",
        }
    }
    params = rt.extract_params(sources)
    assert params["repo_slug"] == "my-repo"
    assert params["path"] == "config/settings.yaml"
    assert params["ref"] == "main"
    assert params["workspace"] == "my-workspace"


# ── run ─────────────────────────────────────────────────────────────

def test_run_returns_file_contents_on_success() -> None:
    mock_contents = {"content": "key: value\nother: data", "size": 22}
    with patch("app.tools.BitbucketFileContentsTool.get_file_contents", return_value=mock_contents):
        result = get_bitbucket_file_contents(
            repo_slug="my-repo",
            path="config/settings.yaml",
            workspace="my-workspace",
            username="user",
            app_password="secret",
        )
    assert result["available"] is True
    assert "content" in result


def test_run_returns_unavailable_on_exception() -> None:
    with patch(
        "app.tools.BitbucketFileContentsTool.get_file_contents",
        side_effect=Exception("404 not found"),
    ):
        result = get_bitbucket_file_contents(
            repo_slug="my-repo",
            path="missing.yaml",
            workspace="my-workspace",
            username="user",
            app_password="secret",
        )
    assert result["available"] is False
    assert "error" in result

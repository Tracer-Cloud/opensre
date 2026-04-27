"""Unit tests for BitbucketCommitsTool (list_bitbucket_commits)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.BitbucketCommitsTool import list_bitbucket_commits
from tests.tools.conftest import BaseToolContract


class TestBitbucketCommitsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_bitbucket_commits.__opensre_registered_tool__


# ── is_available ────────────────────────────────────────────────────

def test_is_available_true_with_full_creds() -> None:
    rt = list_bitbucket_commits.__opensre_registered_tool__
    sources = {
        "bitbucket": {
            "workspace": "my-workspace",
            "username": "user",
            "app_password": "secret",
            "repo_slug": "my-repo",
        }
    }
    assert rt.is_available(sources) is True


def test_is_available_false_missing_repo_slug() -> None:
    rt = list_bitbucket_commits.__opensre_registered_tool__
    sources = {
        "bitbucket": {
            "workspace": "my-workspace",
            "username": "user",
            "app_password": "secret",
        }
    }
    assert rt.is_available(sources) is False


def test_is_available_false_missing_bitbucket_key() -> None:
    rt = list_bitbucket_commits.__opensre_registered_tool__
    assert rt.is_available({}) is False


# ── extract_params ──────────────────────────────────────────────────

def test_extract_params_maps_fields() -> None:
    rt = list_bitbucket_commits.__opensre_registered_tool__
    sources = {
        "bitbucket": {
            "workspace": "my-workspace",
            "username": "user",
            "app_password": "secret",
            "repo_slug": "my-repo",
            "path": "src/main.py",
        }
    }
    params = rt.extract_params(sources)
    assert params["repo_slug"] == "my-repo"
    assert params["path"] == "src/main.py"
    assert params["workspace"] == "my-workspace"
    assert params["username"] == "user"
    assert params["app_password"] == "secret"


# ── run ─────────────────────────────────────────────────────────────

def test_run_returns_commits_on_success() -> None:
    mock_commits = [
        {"hash": "abc123", "message": "fix: resolve crash", "author": "dev"},
        {"hash": "def456", "message": "feat: add feature", "author": "dev"},
    ]
    with patch("app.tools.BitbucketCommitsTool.list_commits", return_value=mock_commits):
        result = list_bitbucket_commits(
            repo_slug="my-repo",
            workspace="my-workspace",
            username="user",
            app_password="secret",
        )
    assert result["available"] is True
    assert len(result["commits"]) == 2
    assert result["commits"][0]["hash"] == "abc123"


def test_run_returns_unavailable_on_exception() -> None:
    with patch(
        "app.tools.BitbucketCommitsTool.list_commits",
        side_effect=Exception("connection refused"),
    ):
        result = list_bitbucket_commits(
            repo_slug="my-repo",
            workspace="my-workspace",
            username="user",
            app_password="secret",
        )
    assert result["available"] is False
    assert "error" in result

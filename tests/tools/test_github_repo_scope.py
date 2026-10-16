"""Tests for GitHub repository scope inference helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from integrations.github.accessible_repos import AccessibleRepository, repository_named_in
from integrations.github.mcp import GitHubMCPConfig
from integrations.github.repo_scope import (
    GITHUB_VCS_REPO_SCOPE_PROVIDER,
    apply_github_repo_scope,
    detect_git_remote_repo_scope,
    find_github_repository_references,
    infer_github_repo_scope,
    parse_github_repository_reference,
    split_repo_full_name,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://github.com/Tracer-Cloud/opensre", ("Tracer-Cloud", "opensre")),
        ("https://github.com/Tracer-Cloud/opensre.git", ("Tracer-Cloud", "opensre")),
        (
            "https://github.com/Tracer-Cloud/opensre/issues/42",
            ("Tracer-Cloud", "opensre"),
        ),
        ("github.com/org/my-repo", ("org", "my-repo")),
        ("repo:Tracer-Cloud/opensre windows crash", ("Tracer-Cloud", "opensre")),
        ("see Tracer-Cloud/opensre for details", ("Tracer-Cloud", "opensre")),
    ],
)
def test_parse_github_repository_reference(text: str, expected: tuple[str, str]) -> None:
    assert parse_github_repository_reference(text) == expected


def test_parse_github_repository_reference_last_match_wins() -> None:
    text = "https://github.com/first/a and https://github.com/second/b"
    assert parse_github_repository_reference(text) == ("second", "b")


def test_find_github_repository_references_keeps_every_distinct_repo() -> None:
    text = "Compare first/a with second/b, then revisit first/a"

    assert find_github_repository_references(text) == (
        ("second", "b"),
        ("first", "a"),
    )


def test_parse_github_repository_reference_rejects_paths() -> None:
    assert parse_github_repository_reference("cli/interactive_shell/chat") is None


def test_split_repo_full_name() -> None:
    assert split_repo_full_name("Tracer-Cloud/opensre.git") == ("Tracer-Cloud", "opensre")
    assert split_repo_full_name("owner-only") == ("", "")


def test_infer_github_repo_scope_prefers_current_message_over_cache() -> None:
    scope = infer_github_repo_scope(
        message="issues in https://github.com/new-org/new-repo",
        conversation_messages=[],
        cached=("old-org", "old-repo"),
    )
    assert scope == ("new-org", "new-repo")


def test_infer_github_repo_scope_uses_conversation_before_cache() -> None:
    scope = infer_github_repo_scope(
        message="do these searches",
        conversation_messages=[
            ("user", "https://github.com/Tracer-Cloud/opensre"),
            ("assistant", "Got it."),
        ],
        cached=("other", "repo"),
    )
    assert scope == ("Tracer-Cloud", "opensre")


def test_infer_github_repo_scope_uses_cache_when_no_explicit_reference() -> None:
    scope = infer_github_repo_scope(
        message="do these searches",
        conversation_messages=[("user", "hello"), ("assistant", "hi")],
        cached=("Tracer-Cloud", "opensre"),
    )
    assert scope == ("Tracer-Cloud", "opensre")


def test_infer_github_repo_scope_uses_github_repository_env() -> None:
    scope = infer_github_repo_scope(
        message="any issues?",
        conversation_messages=[],
        env={"GITHUB_REPOSITORY": "Tracer-Cloud/opensre"},
        cached=None,
    )
    assert scope == ("Tracer-Cloud", "opensre")


def test_detect_git_remote_repo_scope_parses_https_remote() -> None:
    with patch("integrations.github.repo_scope.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="https://github.com/org/repo.git\n")
        assert detect_git_remote_repo_scope("/tmp/repo") == ("org", "repo")
    run.assert_called_once()


def test_detect_git_remote_repo_scope_parses_ssh_remote() -> None:
    with patch("integrations.github.repo_scope.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="git@github.com:org/repo.git\n")
        assert detect_git_remote_repo_scope() == ("org", "repo")


def test_detect_git_remote_repo_scope_falls_back_to_origin() -> None:
    with patch("integrations.github.repo_scope.subprocess.run") as run:
        run.side_effect = [
            MagicMock(returncode=2, stdout=""),
            MagicMock(
                returncode=0,
                stdout="https://github.com/Tracer-Cloud/opensre.git\n",
            ),
        ]

        assert detect_git_remote_repo_scope() == ("Tracer-Cloud", "opensre")

    assert [call.args[0][-1] for call in run.call_args_list] == ["upstream", "origin"]


def test_detect_git_remote_repo_scope_rejects_non_github_remote() -> None:
    with patch("integrations.github.repo_scope.subprocess.run") as run:
        run.side_effect = [
            MagicMock(returncode=0, stdout="git@bitbucket.org:org/repo.git\n"),
            MagicMock(returncode=2, stdout=""),
        ]
        assert detect_git_remote_repo_scope() is None


def test_apply_github_repo_scope_merges_into_github_mcp_config() -> None:
    resolved: dict[str, Any] = {
        "github": GitHubMCPConfig(
            url="https://api.githubcopilot.com/mcp/",
            auth_token="tok",
        )
    }
    merged = apply_github_repo_scope(resolved, "Tracer-Cloud", "opensre")
    gh = merged["github"]
    assert isinstance(gh, dict)
    assert gh["owner"] == "Tracer-Cloud"
    assert gh["repo"] == "opensre"
    assert gh["url"] == "https://api.githubcopilot.com/mcp/"
    assert isinstance(resolved["github"], GitHubMCPConfig)


def test_apply_github_repo_scope_noop_without_github() -> None:
    resolved = {
        "datadog": {
            "connection_origin": "webapp",
            "auth_token": "app-token",
            "connection_verified": True,
        }
    }
    assert apply_github_repo_scope(resolved, "o", "r") == {
        "datadog": {
            "connection_origin": "webapp",
            "auth_token": "app-token",
            "connection_verified": True,
        }
    }


def test_workspace_scope_adds_public_github_source_without_integration() -> None:
    scope = GITHUB_VCS_REPO_SCOPE_PROVIDER.infer(
        message="What is our current GitHub star developer velocity?",
        conversation_messages=[],
        env={"OPENSRE_WORKSPACE_REPO": "Tracer-Cloud/opensre"},
        cwd="/not/a/repository",
        cached=None,
    )
    assert scope is not None

    resolved = GITHUB_VCS_REPO_SCOPE_PROVIDER.apply({}, scope)
    assert resolved == {
        "github": {
            "connection_verified": False,
            "public_repository": True,
            "owner": "Tracer-Cloud",
            "repo": "opensre",
        }
    }


def test_prompt_repository_does_not_enable_public_fallback() -> None:
    scope = GITHUB_VCS_REPO_SCOPE_PROVIDER.infer(
        message="Check stars for other/repository",
        conversation_messages=[],
        env={},
        cwd="/not/a/repository",
        cached=None,
    )
    assert scope is not None

    assert GITHUB_VCS_REPO_SCOPE_PROVIDER.apply({}, scope) == {}


def test_workspace_scope_keeps_configured_github_authenticated() -> None:
    scope = GITHUB_VCS_REPO_SCOPE_PROVIDER.infer(
        message="What is our current GitHub star developer velocity?",
        conversation_messages=[],
        env={"OPENSRE_WORKSPACE_REPO": "Tracer-Cloud/opensre"},
        cwd="/not/a/repository",
        cached=None,
    )
    assert scope is not None

    resolved = GITHUB_VCS_REPO_SCOPE_PROVIDER.apply(
        {
            "github": {
                "connection_origin": "webapp",
                "connection_verified": True,
                "auth_token": "secret",
            }
        },
        scope,
    )

    assert resolved["github"] == {
        "connection_origin": "webapp",
        "connection_verified": True,
        "auth_token": "secret",
        "owner": "Tracer-Cloud",
        "repo": "opensre",
    }


def test_short_repository_name_resolves_to_the_primary_not_a_fork() -> None:
    """'fix the opensre repo' must target the organization's repo, not ask which fork."""
    from core.domain.repositories import set_primary_repository

    set_primary_repository("Tracer-Cloud/opensre")

    scope = GITHUB_VCS_REPO_SCOPE_PROVIDER.infer(
        message="fix the failing issues in the opensre repo",
        conversation_messages=[("assistant", "Forks: KindaJayant/opensre, vrk7/opensre")],
        env={},
        cwd="/not/a/repository",
        cached=("KindaJayant", "opensre"),
    )

    assert scope == ("Tracer-Cloud", "opensre")


_REACHABLE = (
    AccessibleRepository("Tracer-Cloud/opensre", fork=False),
    AccessibleRepository("Tracer-Cloud/opensre-webapp", fork=False),
    AccessibleRepository("vrk7/opensre", fork=True),
)


def test_short_name_resolves_to_the_reachable_original_without_a_preference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack asked "which repository?" for "the opensre repo" the connection could reach."""
    monkeypatch.setattr(
        "integrations.github.repo_scope.accessible_repositories", lambda _token: _REACHABLE
    )

    scope = GITHUB_VCS_REPO_SCOPE_PROVIDER.infer(
        message="fix all the github security issues in the opensre repo",
        conversation_messages=[],
        env={},
        cwd="/not/a/repository",
        cached=None,
    )

    assert scope == ("Tracer-Cloud", "opensre")


def test_a_name_shared_by_two_originals_is_not_guessed() -> None:
    reachable = (*_REACHABLE, AccessibleRepository("other-org/opensre", fork=False))

    assert repository_named_in("fix the opensre repo", reachable) is None


def test_a_placeholder_in_the_assistant_question_is_not_a_repository() -> None:
    """The reply "provide `owner/opensre`" sent the next turn to a 404 repository."""
    history = [
        ("user", "fix all the github security issues in the opensre repo"),
        ("assistant", "Which GitHub repository do you mean? Please provide `owner/opensre`."),
    ]

    scope = GITHUB_VCS_REPO_SCOPE_PROVIDER.infer(
        message="fix it",
        conversation_messages=history,
        env={},
        cwd="/not/a/repository",
        cached=None,
    )

    assert scope is None

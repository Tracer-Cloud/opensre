"""Tests for GitLab repository scope inference helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from integrations.gitlab import GitlabConfig
from integrations.gitlab.repo_scope import (
    apply_gitlab_repo_scope,
    detect_git_remote_repo_scope,
    find_gitlab_repository_references,
    infer_gitlab_repo_scope,
    parse_gitlab_repository_reference,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "https://gitlab.com/marun-group/opensre-test",
            ("marun-group/opensre-test", "", ""),
        ),
        (
            "https://gitlab.com/group/subgroup/project.git",
            ("group/subgroup/project", "", ""),
        ),
        (
            "https://gitlab.com/group/project/-/merge_requests/42",
            ("group/project", "", ""),
        ),
        (
            "read https://gitlab.com/group/project/-/blob/main/runbooks/api.md",
            ("group/project", "main", "runbooks/api.md"),
        ),
        ("check gitlab:group/subgroup/project", ("group/subgroup/project", "", "")),
    ],
)
def test_parse_gitlab_repository_reference(text: str, expected: tuple[str, str, str]) -> None:
    assert parse_gitlab_repository_reference(text) == expected


def test_parse_gitlab_repository_reference_rejects_non_gitlab_url() -> None:
    assert parse_gitlab_repository_reference("https://example.com/group/project") is None


def test_find_gitlab_repository_references_keeps_multiple_projects() -> None:
    text = "Compare gitlab:first/project with https://gitlab.com/second/project"

    assert find_gitlab_repository_references(text) == (
        ("first/project", "", ""),
        ("second/project", "", ""),
    )


def test_infer_gitlab_repo_scope_prefers_message_over_cache() -> None:
    scope = infer_gitlab_repo_scope(
        message="read https://gitlab.com/new/project/-/blob/release/runbook.md",
        cached=("old/project", "main", "old.md"),
    )
    assert scope == ("new/project", "release", "runbook.md")


def test_infer_gitlab_repo_scope_uses_history_before_cache() -> None:
    scope = infer_gitlab_repo_scope(
        message="read that file",
        conversation_messages=[
            ("user", "https://gitlab.com/group/project/-/blob/main/runbook.md"),
            ("assistant", "Got it."),
        ],
        cached=("old/project", "main", "old.md"),
    )
    assert scope == ("group/project", "main", "runbook.md")


def test_infer_gitlab_repo_scope_uses_environment() -> None:
    scope = infer_gitlab_repo_scope(
        message="read the runbook",
        conversation_messages=[],
        env={
            "GITLAB_PROJECT_ID": "group/project",
            "GITLAB_REF": "release",
            "GITLAB_FILE_PATH": "docs/runbook.md",
        },
    )
    assert scope == ("group/project", "release", "docs/runbook.md")


def test_infer_gitlab_repo_scope_recognizes_configured_self_hosted_host() -> None:
    """A self-hosted host that lacks the ``gitlab`` substring is trusted when it
    matches the configured ``GITLAB_BASE_URL`` host."""
    scope = infer_gitlab_repo_scope(
        message="read https://git.internal.corp/team/service/-/blob/main/runbook.md",
        conversation_messages=[],
        env={
            "GITLAB_BASE_URL": "https://git.internal.corp/api/v4",
            "GITLAB_ACCESS_TOKEN": "glpat-token",
        },
    )
    assert scope == ("team/service", "main", "runbook.md")


def test_parse_gitlab_repository_reference_rejects_unknown_self_hosted_host() -> None:
    """Without the configured host context, a non-gitlab host is not recognized."""
    assert (
        parse_gitlab_repository_reference("https://git.internal.corp/team/service/-/blob/main/r.md")
        is None
    )


def test_detect_git_remote_repo_scope_parses_ssh_remote() -> None:
    with patch("integrations.gitlab.repo_scope.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0, stdout="git@gitlab.com:group/subgroup/project.git\n"
        )
        assert detect_git_remote_repo_scope() == ("group/subgroup/project", "", "")


def test_apply_gitlab_repo_scope_merges_into_config() -> None:
    resolved: dict[str, Any] = {
        "gitlab": GitlabConfig(base_url="https://gitlab.com/api/v4", auth_token="token")
    }

    merged = apply_gitlab_repo_scope(resolved, "group/project", "main", "runbooks/api.md")

    gitlab = merged["gitlab"]
    assert isinstance(gitlab, dict)
    assert gitlab["project_id"] == "group/project"
    assert gitlab["ref_name"] == "main"
    assert gitlab["file_path"] == "runbooks/api.md"
    assert gitlab["auth_token"] == "token"
    assert isinstance(resolved["gitlab"], GitlabConfig)


def test_apply_gitlab_repo_scope_noop_without_gitlab() -> None:
    resolved = {"grafana": {"connection_verified": True}}
    assert apply_gitlab_repo_scope(resolved, "group/project", "", "") == resolved

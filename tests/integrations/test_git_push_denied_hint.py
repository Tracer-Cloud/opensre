"""A push refused by GitHub tells the user what the credential lacks; other hosts and failures do not."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from integrations.git import local as git_local
from integrations.git.errors import PUSH_FAILED, GitCommandError
from integrations.git.local import push_branch, push_head_to_upstream

_DENIED = (
    "remote: Permission to o/r.git denied to someone.\n"
    "fatal: unable to access 'https://github.com/o/r.git/': The requested URL returned error: 403"
)
_REJECTED = " ! [rejected] feature -> feature (fetch first)\n"
_HINT = '"Contents: read and write"'


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A checkout on branch ``feature`` with one commit; its remote is set per test."""
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "feature")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    (path / "a.txt").write_text("a\n", encoding="utf-8")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _refusing_push(monkeypatch: pytest.MonkeyPatch, stderr: str) -> None:
    """Let every git command run for real except ``push``, which fails as the remote would."""
    real_run_git = git_local._run_git

    def run_git(workspace: str, *args: str, **kwargs: Any) -> Any:
        if args and args[0] == "push":
            return subprocess.CompletedProcess(["git", *args], 1, stdout="", stderr=stderr)
        return real_run_git(workspace, *args, **kwargs)

    monkeypatch.setattr(git_local, "_run_git", run_git)


def _message_of(push: Callable[[], object]) -> str:
    with pytest.raises(GitCommandError) as raised:
        push()
    assert raised.value.kind == PUSH_FAILED
    return raised.value.message


def test_both_push_paths_name_the_missing_permission_for_a_github_remote(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    _git(repo, "remote", "add", "origin", "https://github.com/o/r.git")
    _refusing_push(monkeypatch, _DENIED)

    # Act
    upstream = _message_of(lambda: push_head_to_upstream(str(repo), token="t"))
    branch = _message_of(lambda: push_branch(str(repo), "feature", token="t"))

    # Assert: git's words first, then the permission to grant, on both paths
    for message in (upstream, branch):
        assert "failed: remote: Permission to o/r.git denied" in message
        assert _HINT in message and "fine-grained token" in message


def test_a_refusal_from_another_host_gets_no_github_advice(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: the same 403, but the remote is not GitHub
    _git(repo, "remote", "add", "origin", "https://gitlab.example.com/o/r.git")
    _refusing_push(monkeypatch, _DENIED.replace("github.com", "gitlab.example.com"))

    # Act
    upstream = _message_of(lambda: push_head_to_upstream(str(repo)))
    branch = _message_of(lambda: push_branch(str(repo), "feature"))

    # Assert
    assert "error: 403" in upstream and "error: 403" in branch
    assert _HINT not in upstream and _HINT not in branch


def test_any_other_push_failure_keeps_gits_words_only(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    _git(repo, "remote", "add", "origin", "https://github.com/o/r.git")
    _refusing_push(monkeypatch, _REJECTED)

    # Act
    message = _message_of(lambda: push_branch(str(repo), "feature"))

    # Assert
    assert message.endswith("failed: ! [rejected] feature -> feature (fetch first)")
    assert _HINT not in message

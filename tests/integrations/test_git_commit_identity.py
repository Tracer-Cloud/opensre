"""Content commits: a person's checkout keeps its author; a host with no git identity still commits."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from integrations.git.local import commit_paths


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def repo_without_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository where neither the checkout nor the machine names a git user."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(key, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


def test_a_host_without_a_git_identity_commits_as_the_opensre_agent(
    repo_without_identity: Path,
) -> None:
    # Arrange
    (repo_without_identity / "fix.py").write_text("x = 1\n", encoding="utf-8")

    # Act
    commit_paths(str(repo_without_identity), ["fix.py"], "fix: lint")

    # Assert: the commit exists and names the agent as author and committer alike
    agent = "OpenSRE Agent <opensreagent@opensre.com>"
    assert _git(repo_without_identity, "log", "-1", "--format=%an <%ae>") == agent
    assert _git(repo_without_identity, "log", "-1", "--format=%cn <%ce>") == agent


def test_an_identity_given_through_the_environment_is_kept(
    repo_without_identity: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: no git config, but the caller names author and committer in the environment
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Env Author")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "author@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Env Committer")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "committer@example.com")
    (repo_without_identity / "fix.py").write_text("x = 1\n", encoding="utf-8")

    # Act
    commit_paths(str(repo_without_identity), ["fix.py"], "fix: lint")

    # Assert: the caller's identity stands; the agent is not substituted for it
    assert _git(repo_without_identity, "log", "-1", "--format=%an <%ae>") == (
        "Env Author <author@example.com>"
    )
    assert _git(repo_without_identity, "log", "-1", "--format=%cn <%ce>") == (
        "Env Committer <committer@example.com>"
    )


def test_a_configured_person_stays_the_author_of_a_content_commit(
    repo_without_identity: Path,
) -> None:
    # Arrange
    _git(repo_without_identity, "config", "user.name", "Pat Example")
    _git(repo_without_identity, "config", "user.email", "pat@example.com")
    (repo_without_identity / "fix.py").write_text("x = 1\n", encoding="utf-8")

    # Act
    commit_paths(str(repo_without_identity), ["fix.py"], "fix: lint")

    # Assert: the person is the author; the agent appears only as co-author
    assert _git(repo_without_identity, "log", "-1", "--format=%an <%ae>") == (
        "Pat Example <pat@example.com>"
    )
    assert "Co-authored-by: OpenSRE Agent" in _git(
        repo_without_identity, "log", "-1", "--format=%B"
    )

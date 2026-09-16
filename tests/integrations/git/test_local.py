"""Tests for the vendor-neutral local-git client (integrations/git).

Exercises the git primitives against a real temp repo with a local bare remote:
repo detection, status parsing (incl. quoted paths), content fingerprints,
protected-branch guards, branch/commit/push, default-branch resolution, and the
host-scoped token auth header.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from config.constants.git import OPENSRE_COMMIT_COAUTHOR_TRAILER
from integrations.git import local as gitlocal
from integrations.git.errors import BRANCH_FAILED, NOT_A_GIT_REPO, PROTECTED_BRANCH, GitCommandError


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _last_commit_message(cwd: Path) -> str:
    return subprocess.run(
        ["git", "log", "-1", "--pretty=%B"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _init_repo(tmp_path: Path) -> Path:
    """A work tree on 'main' with an initial commit and a pushable bare 'origin'."""
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "--bare", str(bare))
    _git(tmp_path, "init", "-b", "main", str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")
    (work / "README.md").write_text("hello\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-u", "origin", "main")
    _git(work, "remote", "set-head", "origin", "main")
    return work


def test_detects_repo_and_default_branch(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    assert gitlocal.is_git_repo(str(work)) is True
    assert gitlocal.current_branch(str(work)) == "main"
    assert gitlocal.default_branch(str(work)) == "main"
    assert gitlocal.changed_paths(str(work)) == []


def test_ensure_git_repo_raises_on_non_repo(tmp_path: Path) -> None:
    with pytest.raises(GitCommandError) as exc:
        gitlocal.ensure_git_repo(str(tmp_path))
    assert exc.value.kind == NOT_A_GIT_REPO


def test_changed_paths_reports_new_and_modified(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    (work / "app").mkdir()
    (work / "app" / "handlers.py").write_text("x = 1\n")
    (work / "README.md").write_text("changed\n")
    paths = set(gitlocal.changed_paths(str(work)))
    assert paths == {"app/handlers.py", "README.md"}


def test_changed_paths_handles_quoted_filenames(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    # A space + non-ASCII byte makes git C-quote the path in default porcelain output.
    weird = "wéird name.txt"
    (work / weird).write_text("x\n")

    # -z returns the path verbatim (unquoted), so downstream git ops match it.
    assert weird in gitlocal.changed_paths(str(work))
    assert gitlocal.file_fingerprints(str(work), [weird])[weird]  # hashable

    gitlocal.create_branch(str(work), "opensre/sentry-fix-1-x", base_default="main")
    gitlocal.commit_paths(str(work), [weird], "add weird")
    assert weird not in gitlocal.changed_paths(str(work))


def test_changed_paths_includes_both_sides_of_a_staged_rename(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    (work / "old.txt").write_text("data\n")
    _git(work, "add", "old.txt")
    _git(work, "commit", "-m", "add old")
    # Stage a rename (git mv) -> porcelain reports an "R" record with new + orig.
    _git(work, "mv", "old.txt", "new.txt")

    paths = set(gitlocal.changed_paths(str(work)))
    assert paths == {"new.txt", "old.txt"}  # the deleted original is included

    # Committing exactly those paths leaves no straggler: old.txt is gone, new.txt exists.
    gitlocal.create_branch(str(work), "opensre/sentry-fix-1-x", base_default="main")
    gitlocal.commit_paths(str(work), list(paths), "rename old -> new")
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=work, capture_output=True, text=True
    ).stdout.split()
    assert "new.txt" in tracked
    assert "old.txt" not in tracked
    assert gitlocal.changed_paths(str(work)) == []  # clean tree, no leftover


def test_file_fingerprints_batches_and_tolerates_missing(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    (work / "a.txt").write_text("aaa\n")
    (work / "b.txt").write_text("bbb\n")

    fps = gitlocal.file_fingerprints(str(work), ["a.txt", "b.txt", "gone.txt"])
    assert fps["a.txt"] and fps["b.txt"]
    assert fps["a.txt"] != fps["b.txt"]
    assert fps["gone.txt"] == ""
    (work / "c.txt").write_text("aaa\n")
    assert gitlocal.file_fingerprints(str(work), ["c.txt"])["c.txt"] == fps["a.txt"]


@pytest.mark.parametrize("branch", ["main", "master", "develop", "trunk", "", "   "])
def test_assert_not_protected_rejects_base_and_empty(branch: str) -> None:
    with pytest.raises(GitCommandError) as exc:
        gitlocal.assert_not_protected(branch, protected_extra="main")
    assert exc.value.kind == PROTECTED_BRANCH


def test_assert_not_protected_rejects_resolved_default() -> None:
    with pytest.raises(GitCommandError):
        gitlocal.assert_not_protected("release-1.0", protected_extra="release-1.0")


def test_create_branch_refuses_protected(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    with pytest.raises(GitCommandError) as exc:
        gitlocal.create_branch(str(work), "main", base_default="main")
    assert exc.value.kind == PROTECTED_BRANCH
    assert gitlocal.current_branch(str(work)) == "main"  # still on base


def test_checkout_branch_switches_to_existing_branch(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    gitlocal.create_branch(str(work), "feature-a", base_default="main")
    gitlocal.checkout_branch(str(work), "main")
    assert gitlocal.current_branch(str(work)) == "main"
    gitlocal.checkout_branch(str(work), "feature-a")
    assert gitlocal.current_branch(str(work)) == "feature-a"


def test_checkout_branch_raises_for_missing_branch(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    with pytest.raises(GitCommandError) as exc:
        gitlocal.checkout_branch(str(work), "does-not-exist")
    assert exc.value.kind == BRANCH_FAILED


def test_branch_commit_push_roundtrip(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    (work / "app").mkdir()
    (work / "app" / "handlers.py").write_text("x = 1\n")
    branch = "opensre/sentry-fix-12345-abc"

    gitlocal.create_branch(str(work), branch, base_default="main")
    gitlocal.commit_paths(str(work), ["app/handlers.py"], "fix: something")
    gitlocal.push_branch(str(work), branch, base_default="main")

    assert gitlocal.current_branch(str(work)) == branch
    remote_branches = subprocess.run(
        ["git", "branch", "-r"], cwd=work, capture_output=True, text=True
    ).stdout
    assert f"origin/{branch}" in remote_branches


def test_commit_paths_adds_opensre_coauthor_trailer(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    (work / "app").mkdir()
    (work / "app" / "handlers.py").write_text("x = 1\n")

    gitlocal.commit_paths(str(work), ["app/handlers.py"], "fix: something")

    message = _last_commit_message(work)
    assert message.splitlines()[0] == "fix: something"
    assert OPENSRE_COMMIT_COAUTHOR_TRAILER in message


def test_commit_paths_does_not_duplicate_opensre_coauthor_trailer(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    (work / "app").mkdir()
    (work / "app" / "handlers.py").write_text("x = 1\n")
    message = f"fix: something\n\n{OPENSRE_COMMIT_COAUTHOR_TRAILER}"

    gitlocal.commit_paths(str(work), ["app/handlers.py"], message)

    assert _last_commit_message(work).count(OPENSRE_COMMIT_COAUTHOR_TRAILER) == 1


def test_commit_paths_isolates_to_given_files(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    (work / "other.txt").write_text("orig\n")
    _git(work, "add", "other.txt")
    _git(work, "commit", "-m", "add other")

    (work / "app").mkdir()
    (work / "app" / "handlers.py").write_text("fix\n")
    (work / "README.md").write_text("wip unstaged\n")
    (work / "other.txt").write_text("wip staged\n")
    _git(work, "add", "other.txt")

    gitlocal.commit_paths(str(work), ["app/handlers.py"], "fix: only that file")

    committed = subprocess.run(
        ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
        cwd=work,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert committed == ["app/handlers.py"]
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=work, capture_output=True, text=True
    ).stdout
    assert "README.md" in status
    assert "other.txt" in status


def test_token_auth_env_injects_host_scoped_header_without_leaking_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    env = gitlocal._token_auth_env("secret-token", "https://github.com/")
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    header = env["GIT_CONFIG_VALUE_0"]
    assert header.startswith("Authorization: Basic ")
    assert "secret-token" not in header


def test_token_auth_env_preserves_existing_git_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "Someone")
    env = gitlocal._token_auth_env("tok", "https://github.com/")
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == "user.name"  # preserved
    assert env["GIT_CONFIG_VALUE_0"] == "Someone"
    assert env["GIT_CONFIG_KEY_1"] == "http.https://github.com/.extraheader"  # appended


def test_default_branch_falls_back_to_remote_head(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    bare = tmp_path / "remote.git"
    _git(work, "remote", "set-head", "origin", "--delete")
    subprocess.run(
        ["git", "--git-dir", str(bare), "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(work, "checkout", "-b", "feature-x")
    assert gitlocal.default_branch(str(work)) == "main"


def test_default_branch_empty_when_unresolvable(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    # No local origin/HEAD and an unreachable remote -> cannot resolve.
    _git(work, "remote", "set-head", "origin", "--delete")
    _git(work, "remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"))
    _git(work, "checkout", "-b", "feature-x")
    # Returns "" rather than guessing the current feature branch.
    assert gitlocal.default_branch(str(work)) == ""


def _fake_run_git_factory(captured: dict[str, Any], origin_url: str):
    def _fake(_ws: str, *args: str, env: Any = None, timeout: Any = None) -> Any:
        if args[:2] == ("remote", "get-url"):
            return subprocess.CompletedProcess(list(args), 0, f"{origin_url}\n", "")
        captured["args"] = args
        captured["env"] = env
        return subprocess.CompletedProcess(list(args), 0, "", "")

    return _fake


def test_push_branch_scopes_token_header_to_https_origin_host(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    captured: dict[str, Any] = {}
    with patch.object(
        gitlocal, "_run_git", _fake_run_git_factory(captured, "https://github.com/acme/app.git")
    ):
        gitlocal.push_branch(str(work), "opensre/sentry-fix-1-x", base_default="main", token="tok")

    assert captured["args"][0] == "push"
    env = captured["env"]
    assert env is not None
    count = int(env["GIT_CONFIG_COUNT"])
    keys = [env[f"GIT_CONFIG_KEY_{i}"] for i in range(count)]
    assert "http.https://github.com/.extraheader" in keys
    assert "http.extraheader" not in keys


@pytest.mark.parametrize(
    "origin_url",
    [
        "git@github.com:acme/app.git",  # SSH
        "http://gitea.local/acme/app.git",  # plaintext HTTP -> never send the token
        "/srv/repos/app.git",  # local/file
    ],
)
def test_push_branch_skips_token_header_for_non_https_origin(
    tmp_path: Path, origin_url: str
) -> None:
    work = _init_repo(tmp_path)
    captured: dict[str, Any] = {}
    with patch.object(gitlocal, "_run_git", _fake_run_git_factory(captured, origin_url)):
        gitlocal.push_branch(str(work), "opensre/sentry-fix-1-x", base_default="main", token="tok")
    assert captured["env"] is None


def test_push_branch_no_env_without_token(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    captured: dict[str, Any] = {}

    def _fake_run_git(_ws: str, *args: str, env: Any = None, timeout: Any = None) -> Any:
        captured["env"] = env
        return subprocess.CompletedProcess(list(args), 0, "", "")

    with patch.object(gitlocal, "_run_git", _fake_run_git):
        gitlocal.push_branch(str(work), "opensre/sentry-fix-1-x", base_default="main")
    assert captured["env"] is None


def test_a_missing_workspace_is_reported_as_such_not_as_missing_git(tmp_path: Path) -> None:
    # Arrange
    missing = str(tmp_path / "nowhere")

    # Act
    with pytest.raises(GitCommandError) as excinfo:
        gitlocal.current_branch(missing)

    # Assert
    assert excinfo.value.kind == NOT_A_GIT_REPO
    assert "is not a directory" in excinfo.value.message


def test_push_head_follows_the_push_remote_gh_sets_for_a_fork_checkout(tmp_path: Path) -> None:
    # Arrange: the branch tracks origin but, as after `gh pr checkout` of a fork PR,
    # pushes to the contributor's fork.
    work = _init_repo(tmp_path)
    fork = tmp_path / "fork.git"
    _git(tmp_path, "init", "--bare", str(fork))
    _git(work, "checkout", "-q", "-b", "feature")
    (work / "README.md").write_text("from the fork\n")
    _git(work, "commit", "-qam", "fork work")
    _git(work, "config", "branch.feature.remote", "origin")
    _git(work, "config", "branch.feature.merge", "refs/heads/feature")
    _git(work, "config", "branch.feature.pushRemote", str(fork))
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True
    ).stdout.strip()

    # Act
    label = gitlocal.push_head_to_upstream(str(work))

    # Assert: the fork received the commit, origin gained no branch, and the
    # label names the fork's owner the way GitHub does.
    fork_heads = subprocess.run(
        ["git", "ls-remote", str(fork), "refs/heads/feature"], capture_output=True, text=True
    ).stdout
    origin_heads = subprocess.run(
        ["git", "ls-remote", str(tmp_path / "remote.git"), "refs/heads/feature"],
        capture_output=True,
        text=True,
    ).stdout
    assert head in fork_heads
    assert origin_heads == ""
    assert label == f"{tmp_path.name}:feature"
    assert gitlocal.push_destination(str(work)) == label


@pytest.mark.parametrize(
    "push_remote",
    [
        "git@github.com:contributor/opensre.git",
        "https://github.com/contributor/opensre.git",
        "ssh://git@github.com/contributor/opensre.git",
    ],
)
def test_push_destination_names_the_fork_owner_for_every_remote_url_form(
    tmp_path: Path, push_remote: str
) -> None:
    # Arrange
    work = _init_repo(tmp_path)
    _git(work, "checkout", "-q", "-b", "feature")
    _git(work, "config", "branch.feature.remote", "origin")
    _git(work, "config", "branch.feature.merge", "refs/heads/feature")
    _git(work, "config", "branch.feature.pushRemote", push_remote)

    # Act
    label = gitlocal.push_destination(str(work))

    # Assert
    assert label == "contributor:feature"

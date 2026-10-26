"""Tests for the local-git merge helpers against a real temp repo."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from integrations.git import (
    MERGE_FAILED,
    GitCommandError,
    abort_merge,
    commit_merge,
    commit_paths,
    describe_conflicts,
    fetch_remote_branch,
    head_sha,
    is_ancestor,
    merge_commit_edits,
    merge_in_progress,
    merge_ref,
    paths_with_conflict_markers,
    stage_paths,
    unmerged_paths,
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _diverged_repo(tmp_path: Path) -> Path:
    """Work tree on ``feature`` whose ``origin/main`` changed the same line and deleted a file."""
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "--bare", str(bare))
    _git(tmp_path, "init", "-b", "main", str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")
    (work / "shared.txt").write_text("base\n")
    (work / "doomed.txt").write_text("keep?\n")
    (work / "untouched.txt").write_text("same\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-u", "origin", "main")

    _git(work, "checkout", "-b", "feature")
    (work / "shared.txt").write_text("feature\n")
    (work / "doomed.txt").write_text("feature edit\n")
    _git(work, "commit", "-am", "feature change")

    _git(work, "checkout", "main")
    (work / "shared.txt").write_text("main\n")
    _git(work, "rm", "-q", "doomed.txt")
    (work / "only-main.txt").write_text("new on main\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "main change")
    _git(work, "push", "origin", "main")
    _git(work, "reset", "--hard", "HEAD~1")
    _git(work, "checkout", "feature")
    return work


def test_merge_ref_stops_on_conflicts_and_describes_each_side(tmp_path: Path) -> None:
    # Arrange
    work = _diverged_repo(tmp_path)
    fetch_remote_branch(str(work), "main")

    # Act
    merged = merge_ref(str(work), "origin/main", message="Merge main")

    # Assert
    assert merged is False
    assert merge_in_progress(str(work)) is True
    assert sorted(unmerged_paths(str(work))) == ["doomed.txt", "shared.txt"]
    described = {
        c.path: c.description for c in describe_conflicts(str(work), ours="feature", theirs="main")
    }
    assert described == {
        "shared.txt": "changed on both feature and main",
        "doomed.txt": "changed on feature, deleted on main",
    }
    assert paths_with_conflict_markers(str(work), ["shared.txt", "doomed.txt"]) == ["shared.txt"]


def test_resolved_merge_commits_with_both_parents(tmp_path: Path) -> None:
    # Arrange
    work = _diverged_repo(tmp_path)
    fetch_remote_branch(str(work), "main")
    merge_ref(str(work), "origin/main", message="Merge main into feature")
    (work / "shared.txt").write_text("feature+main\n")
    (work / "doomed.txt").unlink()

    # Act
    stage_paths(str(work), ["shared.txt", "doomed.txt"])
    sha = commit_merge(str(work))

    # Assert
    assert unmerged_paths(str(work)) == []
    assert merge_in_progress(str(work)) is False
    assert sha == head_sha(str(work))
    assert len(_git(work, "log", "-1", "--pretty=%P").split()) == 2
    assert is_ancestor(str(work), "origin/main", "HEAD") is True
    message = _git(work, "log", "-1", "--pretty=%B")
    assert "Merge main into feature" in message
    assert "# Conflicts:" not in message
    assert not (work / "doomed.txt").exists()
    assert (work / "only-main.txt").read_text() == "new on main\n"


def test_a_tracked_package_under_an_ignore_rule_stages_and_commits(tmp_path: Path) -> None:
    """A package an ``output/`` rule matches stays tracked; git refuses ``add <path>`` there.

    Merging main into a live PR failed with "git add failed: ... is ignored by one
    of your .gitignore files" because main changed such a package.
    """
    # Arrange: a tracked file under an ignored directory, changed on main.
    work = _diverged_repo(tmp_path)
    _git(work, "checkout", "main")
    _git(work, "reset", "--hard", "origin/main")
    (work / ".gitignore").write_text("output/\n")
    (work / "pkg" / "output").mkdir(parents=True)
    (work / "pkg" / "output" / "mod.py").write_text("v1\n")
    _git(work, "add", ".gitignore")
    _git(work, "add", "-f", "pkg/output/mod.py")
    _git(work, "commit", "-m", "tracked package under output/")
    (work / "pkg" / "output" / "mod.py").write_text("v2\n")
    _git(work, "commit", "-am", "change the package")
    _git(work, "push", "origin", "main")
    _git(work, "checkout", "feature")
    fetch_remote_branch(str(work), "main")
    merge_ref(str(work), "origin/main", message="Merge main")
    (work / "shared.txt").write_text("feature+main\n")
    (work / "doomed.txt").unlink()

    # Act
    stage_paths(str(work), ["shared.txt", "doomed.txt", "pkg/output/mod.py"])
    commit_merge(str(work))
    (work / "pkg" / "output" / "mod.py").write_text("v3\n")
    commit_paths(str(work), ["pkg/output/mod.py"], "edit the package")

    # Assert
    assert unmerged_paths(str(work)) == []
    assert _git(work, "show", "HEAD:pkg/output/mod.py") == "v3"
    assert _git(work, "status", "--porcelain") == ""


def test_stage_paths_accepts_a_deletion_the_resolver_already_staged(tmp_path: Path) -> None:
    # Arrange: the resolver ran ``git rm`` itself, so the path is in neither index nor tree.
    work = _diverged_repo(tmp_path)
    fetch_remote_branch(str(work), "main")
    merge_ref(str(work), "origin/main", message="Merge main")
    (work / "shared.txt").write_text("feature+main\n")
    _git(work, "rm", "-q", "doomed.txt")

    # Act
    stage_paths(str(work), ["shared.txt", "doomed.txt"])
    commit_merge(str(work))

    # Assert
    assert unmerged_paths(str(work)) == []
    assert not (work / "doomed.txt").exists()
    assert "doomed.txt" not in _git(work, "ls-files")


def test_abort_merge_restores_pre_merge_head(tmp_path: Path) -> None:
    # Arrange
    work = _diverged_repo(tmp_path)
    before = head_sha(str(work))
    fetch_remote_branch(str(work), "main")
    merge_ref(str(work), "origin/main", message="Merge main")

    # Act
    abort_merge(str(work))

    # Assert
    assert merge_in_progress(str(work)) is False
    assert head_sha(str(work)) == before
    assert (work / "shared.txt").read_text() == "feature\n"


def test_merge_ref_raises_on_non_conflict_failure(tmp_path: Path) -> None:
    # Arrange
    work = _diverged_repo(tmp_path)

    # Act / Assert: the ref was never fetched, so git fails without any conflict.
    with pytest.raises(GitCommandError) as excinfo:
        merge_ref(str(work), "origin/does-not-exist", message="Merge")
    assert excinfo.value.kind == MERGE_FAILED
    assert merge_in_progress(str(work)) is False


def test_fetch_uses_explicit_token_without_putting_it_in_argv(monkeypatch) -> None:
    import subprocess

    from integrations.git import merge

    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    calls = []

    def run(workspace, *args, **kwargs):
        calls.append((workspace, args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(merge, "_run_git", run)
    monkeypatch.setattr(
        merge, "_remote_https_base", lambda *_args: "https://github.com/", raising=False
    )
    fetch_remote_branch("/checkout", "demo/repair", token="private-demo-token")
    _, args, kwargs = calls[-1]
    assert args == ("fetch", "origin", "refs/heads/demo/repair:refs/remotes/origin/demo/repair")
    assert "private-demo-token" not in repr(args)
    env = kwargs["env"]
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert env["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")


def test_merge_commit_edits_are_the_resolver_changes_not_either_side(tmp_path: Path) -> None:
    """Base-only and head-only changes belong to a parent; hand edits differ from both."""
    # Arrange: stop on the conflict, resolve it, and sneak in an unrelated edit.
    work = _diverged_repo(tmp_path)
    fetch_remote_branch(str(work), "main")
    assert merge_ref(str(work), "origin/main", message="merge main") is False
    (work / "shared.txt").write_text("resolved\n")
    (work / "untouched.txt").write_text("weakened\n")
    _git(work, "rm", "-q", "--cached", "doomed.txt")
    (work / "doomed.txt").unlink()
    stage_paths(str(work), ["shared.txt", "untouched.txt"])
    merged = commit_merge(str(work))

    # Act
    edited = merge_commit_edits(str(work), merged)

    # Assert: only-main.txt (base) and the head's own history are not edits.
    assert edited == ["shared.txt", "untouched.txt"]

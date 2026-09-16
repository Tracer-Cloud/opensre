"""Tests for checking a pull request out into a clone of OpenSRE's own."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from integrations.git import GitCommandError, merge_in_progress
from integrations.github import checkout_pull_request, parse_pull_request


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _github_checkout(tmp_path: Path) -> Path:
    work = tmp_path / "cwd"
    _git(tmp_path, "init", "-q", str(work))
    _git(work, "remote", "add", "origin", "https://github.com/Tracer-Cloud/opensre.git")
    return work


def _fake_clone(url: str, workspace: str, *, token: str | None = None) -> None:
    """Stand in for the GitHub clone: a repository with one commit on main."""
    del url, token
    path = Path(workspace)
    _git(path.parent, "init", "-q", "-b", "main", str(path))
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Tester")
    (path / "app.py").write_text("greeting = 'hello'\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")


def _stop_a_merge(path: Path) -> None:
    """Leave the clone with a conflicted merge in progress."""
    _git(path, "checkout", "-q", "-b", "other")
    (path / "app.py").write_text("greeting = 'other'\n")
    _git(path, "commit", "-qam", "other")
    _git(path, "checkout", "-q", "pr-7")
    (path / "app.py").write_text("greeting = 'pr'\n")
    _git(path, "commit", "-qam", "pr")
    subprocess.run(["git", "merge", "other"], cwd=path, capture_output=True, text=True)
    assert merge_in_progress(str(path))


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ("https://github.com/Acme/widgets/pull/12", ("Acme", "widgets", 12)),
        ("Acme/widgets#12", ("Acme", "widgets", 12)),
        ("#7", ("Tracer-Cloud", "opensre", 7)),
        ("7", ("Tracer-Cloud", "opensre", 7)),
    ],
)
def test_selectors_name_a_pull_request_falling_back_to_the_current_origin(
    tmp_path: Path, selector: str, expected: tuple[str, str, int]
) -> None:
    # Arrange
    work = _github_checkout(tmp_path)

    # Act / Assert
    assert parse_pull_request(selector, cwd=str(work)) == expected


def test_a_bare_number_outside_a_github_checkout_asks_for_the_repository(tmp_path: Path) -> None:
    # Arrange / Act
    with pytest.raises(GitCommandError) as excinfo:
        parse_pull_request("7", cwd=str(tmp_path))

    # Assert
    assert "owner/repo#7" in excinfo.value.message


def test_the_pull_request_is_checked_out_in_a_clone_of_its_own(tmp_path: Path) -> None:
    # Arrange
    work = _github_checkout(tmp_path)
    root = tmp_path / "merges"
    gh_calls: list[tuple[list[str], dict[str, Any]]] = []

    def run_gh(args: list[str], **kwargs: Any) -> str:
        gh_calls.append((args, kwargs))
        _git(Path(kwargs["cwd"]), "checkout", "-q", "-b", "pr-7")
        return ""

    # Act
    checkout = checkout_pull_request(
        "7", cwd=str(work), root=root, clone=_fake_clone, run_gh=run_gh
    )

    # Assert: the clone sits under the root, the head is checked out there, and
    # the user's own checkout was never touched.
    assert checkout.workspace == str(root / "Tracer-Cloud" / "opensre" / "7")
    assert checkout.head_branch == "pr-7"
    assert checkout.reused is False
    assert checkout.label == "Tracer-Cloud/opensre#7"
    assert gh_calls == [
        (
            ["pr", "checkout", "7"],
            {"repo": "Tracer-Cloud/opensre", "github_token": None, "cwd": checkout.workspace},
        )
    ]
    assert not (work / "app.py").exists()


def test_a_clone_left_mid_merge_is_reused_instead_of_replaced(tmp_path: Path) -> None:
    # Arrange: a first checkout whose merge stopped on conflicts.
    work = _github_checkout(tmp_path)
    root = tmp_path / "merges"
    clones: list[str] = []

    def clone(url: str, workspace: str, *, token: str | None = None) -> None:
        clones.append(workspace)
        _fake_clone(url, workspace, token=token)

    def run_gh(args: list[str], **kwargs: Any) -> str:
        del args
        _git(Path(kwargs["cwd"]), "checkout", "-q", "-b", "pr-7")
        return ""

    first = checkout_pull_request("7", cwd=str(work), root=root, clone=clone, run_gh=run_gh)
    _stop_a_merge(Path(first.workspace))

    # Act
    again = checkout_pull_request("7", cwd=str(work), root=root, clone=clone, run_gh=run_gh)

    # Assert
    assert again.reused is True
    assert again.workspace == first.workspace
    assert clones == [first.workspace]
    assert merge_in_progress(first.workspace)


@pytest.mark.parametrize(
    "selector",
    [
        "https://github.com/..\\..\\outside/keep/pull/1",
        "https://github.com/../evil/pull/1",
        "https://github.com/foo\\bar/baz/pull/1",
    ],
)
def test_a_url_that_escapes_the_merge_root_is_rejected(tmp_path: Path, selector: str) -> None:
    # Arrange: a sibling directory that a backslash/parent selector must not delete.
    work = _github_checkout(tmp_path)
    root = tmp_path / "merges"
    outside = tmp_path / "outside"
    outside.mkdir()
    keep = outside / "keep.txt"
    keep.write_text("safe")

    def run_gh(*args: object, **kwargs: object) -> str:
        del args, kwargs
        raise AssertionError("gh must not run for an escaped selector")

    # Act
    with pytest.raises(GitCommandError) as excinfo:
        checkout_pull_request(selector, cwd=str(work), root=root, clone=_fake_clone, run_gh=run_gh)

    # Assert
    assert excinfo.value.kind == "invalid_input"
    assert keep.exists()
    assert keep.read_text() == "safe"
    assert not root.exists()


def test_hyphenated_owner_and_repo_do_not_share_a_workspace(tmp_path: Path) -> None:
    # Arrange: a/b-c#7 and a-b/c#7 used to flatten to the same a-b-c-7 directory.
    work = _github_checkout(tmp_path)
    root = tmp_path / "merges"
    clones: list[str] = []

    def clone(url: str, workspace: str, *, token: str | None = None) -> None:
        clones.append(workspace)
        _fake_clone(url, workspace, token=token)

    def run_gh(args: list[str], **kwargs: Any) -> str:
        del args
        _git(Path(kwargs["cwd"]), "checkout", "-q", "-b", "pr-7")
        return ""

    first = checkout_pull_request("a/b-c#7", cwd=str(work), root=root, clone=clone, run_gh=run_gh)
    _stop_a_merge(Path(first.workspace))

    # Act
    other = checkout_pull_request("a-b/c#7", cwd=str(work), root=root, clone=clone, run_gh=run_gh)

    # Assert
    assert first.workspace == str(root / "a" / "b-c" / "7")
    assert other.workspace == str(root / "a-b" / "c" / "7")
    assert other.reused is False
    assert clones == [first.workspace, other.workspace]
    assert merge_in_progress(first.workspace)

"""Tests for architecture issue tool repo workspace helpers."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from integrations.github.tools.architecture_issue_tool.repo_workspace import (
    WorkspaceError,
    architecture_workspace_dir,
    cleanup_architecture_workspace,
    clone_github_repo,
    cloned_github_repo,
    github_remote_url,
    prepare_architecture_workspace,
)


@pytest.fixture
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the shared architecture workspace root at a per-test directory."""
    root = tmp_path / "opensre" / "workspace"
    monkeypatch.setattr(
        "integrations.github.tools.architecture_issue_tool.repo_workspace.architecture_workspace_dir",
        lambda: root,
    )
    return root


def _fake_clone(**kwargs: object) -> None:
    """Stand in for git: write a marker into the clone destination."""
    destination = kwargs["destination"]
    assert isinstance(destination, Path)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "README.md").write_text("ok\n", encoding="utf-8")


def test_github_remote_url() -> None:
    assert (
        github_remote_url("Tracer-Cloud", "opensre")
        == "https://github.com/Tracer-Cloud/opensre.git"
    )


def test_cloned_github_repo_uses_local_path_without_clone(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    marker.write_text("stay", encoding="utf-8")

    with cloned_github_repo("org", "repo", local_path=str(tmp_path)) as workspace:
        assert workspace.root == tmp_path.resolve()
        assert workspace.owner == "org"
        assert workspace.repo == "repo"
        assert marker.read_text(encoding="utf-8") == "stay"

    assert marker.exists()


def test_cloned_github_repo_local_path_must_exist(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with (
        pytest.raises(WorkspaceError, match="not a directory"),
        cloned_github_repo("org", "repo", local_path=str(missing)),
    ):
        pass


def test_cloned_github_repo_requires_owner_and_repo() -> None:
    with pytest.raises(WorkspaceError, match="owner and repo"), cloned_github_repo("", "repo"):
        pass


def test_architecture_workspace_dir_is_under_opensre_tmp() -> None:
    from config.constants.paths import OPENSRE_TMP_DIR

    workspace = architecture_workspace_dir()
    assert workspace == OPENSRE_TMP_DIR / "workspace"


@pytest.mark.parametrize("target", ["outside", "shared_root"])
def test_cleanup_refuses_anything_but_an_audit_directory(
    target: str, tmp_path: Path, workspace_root: Path
) -> None:
    """Cleanup must never delete outside the root, nor the root other audits share."""
    audit = prepare_architecture_workspace()
    (audit / "README.md").write_text("live\n", encoding="utf-8")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    path = outside if target == "outside" else workspace_root

    with pytest.raises(WorkspaceError, match="not an audit directory"):
        cleanup_architecture_workspace(path)

    assert outside.exists()
    assert (audit / "README.md").exists()


def test_cleanup_architecture_workspace_surfaces_rmtree_errors(workspace_root: Path) -> None:
    audit = prepare_architecture_workspace()
    (audit / "stale.txt").write_text("x", encoding="utf-8")

    with (
        patch(
            "integrations.github.tools.architecture_issue_tool.repo_workspace.shutil.rmtree",
            side_effect=OSError("Permission denied"),
        ),
        pytest.raises(WorkspaceError, match="could not remove"),
    ):
        cleanup_architecture_workspace(audit)


def test_cleanup_architecture_workspace_fails_if_path_still_exists(workspace_root: Path) -> None:
    audit = prepare_architecture_workspace()

    def _noop_rmtree(path: object, *args: object, **kwargs: object) -> None:
        return None

    with (
        patch(
            "integrations.github.tools.architecture_issue_tool.repo_workspace.shutil.rmtree",
            side_effect=_noop_rmtree,
        ),
        pytest.raises(WorkspaceError, match="still exists"),
    ):
        cleanup_architecture_workspace(audit)


@patch("integrations.github.tools.architecture_issue_tool.repo_workspace._shallow_clone")
def test_concurrent_audits_get_separate_clones_and_clean_up_independently(
    mock_shallow_clone, workspace_root: Path
) -> None:
    """A second audit starting mid-scan must not touch the first audit's clone."""
    mock_shallow_clone.side_effect = _fake_clone

    first = clone_github_repo("org", "first", ref="main")
    second = clone_github_repo("org", "second", ref="main")

    assert first.root != second.root
    assert first.root.parent == second.root.parent == workspace_root
    assert (first.root / "README.md").exists()

    cleanup_architecture_workspace(second.root)

    assert not second.root.exists()
    assert (first.root / "README.md").exists()
    cleanup_architecture_workspace(first.root)
    assert list(workspace_root.iterdir()) == []


def test_only_the_session_that_started_an_audit_can_delete_its_clone(
    workspace_root: Path,
) -> None:
    """Another session naming a live audit's directory must not interrupt that audit."""
    alices = prepare_architecture_workspace(audit_owner="session-alice")
    (alices / "README.md").write_text("live\n", encoding="utf-8")
    unknown = workspace_root / "audit-from-another-process"
    unknown.mkdir()

    with pytest.raises(WorkspaceError, match="not an audit this session started"):
        cleanup_architecture_workspace(alices, audit_owner="session-bob")
    with pytest.raises(WorkspaceError, match="not an audit this session started"):
        cleanup_architecture_workspace(unknown, audit_owner="session-bob")

    assert (alices / "README.md").exists() and unknown.exists()
    cleanup_architecture_workspace(alices, audit_owner="session-alice")
    assert not alices.exists()


def test_prepare_sweeps_only_abandoned_entries(workspace_root: Path) -> None:
    """Leftovers from crashed audits go; a live audit's directory survives a new audit."""
    live = prepare_architecture_workspace()
    (live / "README.md").write_text("live\n", encoding="utf-8")
    abandoned = workspace_root / "audit-crashed"
    abandoned.mkdir()
    (abandoned / "README.md").write_text("old\n", encoding="utf-8")
    two_days_ago = time.time() - 2 * 24 * 60 * 60
    os.utime(abandoned, (two_days_ago, two_days_ago))

    fresh = prepare_architecture_workspace()

    assert not abandoned.exists()
    assert (live / "README.md").exists()
    assert fresh.exists() and fresh != live


@patch("integrations.github.tools.architecture_issue_tool.repo_workspace._shallow_clone")
@patch(
    "integrations.github.tools.architecture_issue_tool.repo_workspace._remote_default_branch",
    return_value="main",
)
def test_cloned_github_repo_clones_and_cleans_up(
    mock_default_branch,
    mock_shallow_clone,
    workspace_root: Path,
) -> None:
    mock_shallow_clone.side_effect = _fake_clone

    with cloned_github_repo("Tracer-Cloud", "opensre", token="ghp_test") as result:
        assert result.ref == "main"
        assert result.root.parent == workspace_root
        mock_default_branch.assert_called_once()
        assert (result.root / "README.md").exists()

    assert not result.root.exists()
    assert workspace_root.exists()


@patch("integrations.github.tools.architecture_issue_tool.repo_workspace._shallow_clone")
def test_clone_github_repo_cleans_up_on_clone_failure(
    mock_shallow_clone,
    workspace_root: Path,
) -> None:
    mock_shallow_clone.side_effect = WorkspaceError("git clone failed")

    with pytest.raises(WorkspaceError, match="git clone failed"):
        clone_github_repo("Tracer-Cloud", "opensre", ref="main")

    assert list(workspace_root.iterdir()) == []


def test_shallow_clone_sha_fetches_commit_directly(tmp_path: Path) -> None:
    """Non-HEAD SHAs must use init+fetch, not clone --depth 1 + checkout."""
    from subprocess import CompletedProcess

    from integrations.github.tools.architecture_issue_tool.repo_workspace import _shallow_clone

    destination = tmp_path / "workspace"
    sha = "abcdef0123456789abcdef0123456789abcdef01"
    calls: list[tuple[str, ...]] = []

    def _fake_run_git(cwd: Path, *args: str, **_kwargs: object) -> CompletedProcess[str]:
        calls.append(args)
        return CompletedProcess(args=("git", *args), returncode=0, stdout="", stderr="")

    with patch(
        "integrations.github.tools.architecture_issue_tool.repo_workspace._run_git",
        side_effect=_fake_run_git,
    ):
        _shallow_clone(
            remote_url="https://github.com/org/repo.git",
            destination=destination,
            ref=sha,
            token=None,
        )

    assert calls[0] == ("init",)
    assert calls[1] == ("remote", "add", "origin", "https://github.com/org/repo.git")
    assert calls[2] == ("fetch", "--depth", "1", "origin", sha)
    assert calls[3] == ("checkout", "--detach", "FETCH_HEAD")
    assert not any(args[:1] == ("clone",) for args in calls)

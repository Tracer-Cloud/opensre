"""Tests for the local git workspace scan tool."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from rich.console import Console

from tests.tools.conftest import BaseToolContract
from tools.system.workspace_git_scan.render import render_snapshot, snapshot_text
from tools.system.workspace_git_scan.scan import parse_github_remote, scan_workspace
from tools.system.workspace_git_scan.tool import scan_local_git_workspace


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _repo(root: Path, name: str, *, origin: str, commits: int, workflows: bool) -> Path:
    path = root / name
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Tester")
    if origin:
        _git(path, "remote", "add", "origin", origin)
    if workflows:
        (path / ".github" / "workflows").mkdir(parents=True)
        (path / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    for index in range(commits):
        (path / f"f{index}.txt").write_text(str(index))
        _git(path, "add", "-A")
        _git(path, "commit", "-qm", f"c{index}")
    return path


def test_scan_folds_clones_and_skips_nested_and_ignored_dirs(tmp_path: Path) -> None:
    # Arrange: two clones of one GitHub repo, one plain repo, one under node_modules.
    _repo(tmp_path / "work", "app", origin="git@github.com:acme/app.git", commits=3, workflows=True)
    clone = _repo(
        tmp_path / "work",
        "app-copy",
        origin="https://github.com/acme/app",
        commits=2,
        workflows=False,
    )
    (clone / "dirty.txt").write_text("wip")
    _repo(tmp_path, "notes", origin="", commits=1, workflows=False)
    (tmp_path / "notes" / ".github" / "workflows").mkdir(parents=True)
    _repo(tmp_path / "node_modules", "dep", origin="", commits=1, workflows=False)

    # Act
    snapshot = scan_workspace(tmp_path, days=30)

    # Assert
    names = [repo.name for repo in snapshot.repos]
    assert names == ["app", "notes"]
    app = snapshot.repos[0]
    assert app.github_full_name == "acme/app"
    assert app.commits == 3
    assert app.uncommitted == 1
    assert app.has_workflows is True
    assert snapshot.repos[1].has_workflows is False
    assert snapshot.total_commits == 4


def test_parse_github_remote_accepts_ssh_and_https() -> None:
    assert parse_github_remote("git@github.com:acme/app.git") == ("acme", "app")
    assert parse_github_remote("https://github.com/acme/app") == ("acme", "app")
    assert parse_github_remote("https://gitlab.com/acme/app.git") == ("", "")


def test_render_groups_the_tail_into_all_others(tmp_path: Path) -> None:
    for index, commits in enumerate((6, 5, 4, 3, 2, 1)):
        _repo(tmp_path, f"r{index}", origin="", commits=commits, workflows=False)
    snapshot = scan_workspace(tmp_path, days=30)
    console = Console(record=True, width=100, force_terminal=False)

    render_snapshot(console, snapshot)
    text = snapshot_text(snapshot)

    exported = console.export_text()
    assert "Git repos found" in exported and "6" in exported
    assert "all others" in exported
    assert text.splitlines()[-1].startswith("all others")
    assert text.count("█") > 0


def test_tool_rejects_missing_root() -> None:
    result = scan_local_git_workspace(root="/definitely/not/here")

    assert result["success"] is False
    assert "nothing was scanned" in result["response_text"]


def test_tool_includes_chart_text_when_no_console_is_available(tmp_path: Path) -> None:
    _repo(tmp_path, "solo", origin="https://github.com/acme/solo", commits=2, workflows=True)

    result = scan_local_git_workspace(root=str(tmp_path))

    assert result["success"] is True
    assert result["rendered_in_shell"] is False
    assert result["repos"][0]["github"] == "acme/solo"
    assert result["repos_with_workflows"] == 1
    assert "Activity (commits, last 30 days)" in result["response_text"]


class TestScanLocalGitWorkspaceContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return scan_local_git_workspace.__opensre_registered_tool__

"""Tests for the local git workspace scan tool."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from config.constants.git import GIT_OPTIONAL_LOCKS_ENV, GIT_TERMINAL_PROMPT_ENV
from core.agent_harness.tools.tool_context import (
    ACTION_TOOL_CONTEXT_RESOURCE_KEY,
    ActionToolScope,
)
from core.tool.contracts import REGISTERED_TOOL_ATTR, AgentToolContext
from tests.tools.conftest import BaseToolContract
from tools.system.workspace_git_scan.render import render_snapshot, snapshot_text
from tools.system.workspace_git_scan.scan import (
    ScanStop,
    measure_repo,
    parse_github_remote,
    scan_workspace,
)
from tools.system.workspace_git_scan.skips import default_skip_paths
from tools.system.workspace_git_scan.tool import scan_local_git_workspace

_SCAN_MODULE = "tools.system.workspace_git_scan.scan"
_ALWAYS_SKIPPED = ("Applications", "Movies", "Music", "Pictures", "Public")
_MACOS_PROTECTED = ("Desktop", "Documents", "Downloads")


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


def _checkout(root: Path, name: str, *, used_at: float = 1_000.0) -> Path:
    """A folder the walk takes for a checkout, last worked in at *used_at* (epoch seconds)."""
    path = root / name
    (path / ".git").mkdir(parents=True)
    index = path / ".git" / "index"
    index.touch()
    os.utime(index, (used_at, used_at))
    return path


def _commit(repo: Path, *, email: str, days_ago: int) -> None:
    """An empty commit authored by *email*, authored and committed *days_ago* days ago."""
    when = f"{int(time.time()) - days_ago * 86_400} +0000"
    env = {
        **os.environ,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_AUTHOR_DATE": when,
        "GIT_COMMITTER_DATE": when,
    }
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", f"by {email or 'nobody'}"],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
    )


def _ignore(_checkout: Path) -> None:
    return None


def _without_git(
    monkeypatch: pytest.MonkeyPatch, on_status: Callable[[Path], None] = _ignore
) -> None:
    """Answer every git call with no output; *on_status* sees each checkout's ``git status``."""

    def git(repo_dir: Path, *args: str) -> str:
        if args[0] == "status":
            on_status(repo_dir)
        return ""

    monkeypatch.setattr(f"{_SCAN_MODULE}._git", git)


def _git_calls_at_once(monkeypatch: pytest.MonkeyPatch, count: int) -> None:
    monkeypatch.setattr(f"{_SCAN_MODULE}._MEASURE_WORKERS", count)


def _record_git(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Let git run and record ``(folder name, subcommand)`` for every call."""
    calls: list[tuple[str, str]] = []
    real_run = subprocess.run

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((Path(args[2]).name, args[3]))
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


class _Clock:
    """Monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _TurnConsole:
    """The turn console: ESC sets ``cancel_requested``; prints are recorded."""

    def __init__(self) -> None:
        self.cancel_requested = False
        self.printed: list[Any] = []

    def print(self, *args: Any, **_kwargs: Any) -> None:
        self.printed.extend(args)


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
    assert snapshot.stop_reason is None


def test_worktrees_share_one_history_read_and_keep_their_own_uncommitted_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A repository often has many worktrees (a branch or agent session each): their
    # history is one, so git log runs once, while every worktree has its own status.
    main = _repo(tmp_path, "app", origin="", commits=3, workflows=False)
    feature, fix = tmp_path / "app-feature", tmp_path / "app-fix"
    _git(main, "worktree", "add", "-q", "-b", "feature", str(feature))
    _git(main, "worktree", "add", "-q", "-b", "fix", str(fix))
    # A relative pointer, as submodules and worktree.useRelativePaths write it.
    own_git_dir = Path((fix / ".git").read_text().removeprefix("gitdir:").strip())
    relative = os.path.relpath(own_git_dir.resolve(), fix.resolve())
    (fix / ".git").write_text(f"gitdir: {relative}\n")
    (main / "notes.txt").write_text("todo")
    (main / "draft.txt").write_text("draft")
    (fix / "wip.txt").write_text("wip")
    calls = _record_git(monkeypatch)

    snapshot = scan_workspace(tmp_path, days=30)

    rows = {repo.name: (repo.commits, repo.uncommitted) for repo in snapshot.repos}
    assert rows == {"app": (3, 2), "app-feature": (3, 0), "app-fix": (3, 1)}
    assert [command for _, command in calls].count("log") == 1


def test_one_log_counts_what_rev_list_counted_and_own_commits_ignore_email_case(
    tmp_path: Path,
) -> None:
    # The two rev-list --count calls became one git log; the counts must not move.
    repo = _repo(tmp_path, "app", origin="", commits=0, workflows=False)
    _commit(repo, email="me@example.com", days_ago=90)  # outside the window
    _commit(repo, email="other@example.com", days_ago=3)
    _commit(repo, email="Me@Example.com", days_ago=2)
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, email="ME@example.com", days_ago=1)  # only on a branch not checked out
    _git(repo, "checkout", "-q", "main")
    _commit(repo, email="", days_ago=0)  # newest, so an empty first line of the log

    activity = measure_repo(repo, days=30, author="me@example.com")

    rev_list = subprocess.run(
        ["git", "rev-list", "--count", "--all", "--since=30.days"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert activity.commits == int(rev_list.stdout) == 4
    assert activity.own_commits == 2


def test_origin_comes_from_the_config_file_unless_a_url_rewrite_applies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # git remote get-url applies url.<base>.insteadOf rules, so a rewritable origin
    # is left to git; a plain one is read from the config file without a git call.
    user_config = tmp_path / "gitconfig"
    user_config.write_text('[url "https://github.com/"]\n\tinsteadOf = gh:\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(user_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    workspace = tmp_path / "work"
    _repo(workspace, "app", origin="git@github.com:acme/app.git", commits=1, workflows=False)
    _repo(workspace, "tool", origin="gh:acme/tool", commits=1, workflows=False)
    calls = _record_git(monkeypatch)

    snapshot = scan_workspace(workspace, days=30)

    origins = {repo.name: (repo.origin, repo.github_full_name) for repo in snapshot.repos}
    assert origins == {
        "app": ("git@github.com:acme/app.git", "acme/app"),
        "tool": ("https://github.com/acme/tool", "acme/tool"),
    }
    assert ("tool", "remote") in calls
    assert ("app", "remote") not in calls


def test_git_never_reads_the_terminal_or_takes_the_index_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A child inheriting the shell's stdin can block on a prompt; a plain
    # ``git status`` rewrites .git/index although the tool claims to be read-only.
    calls: list[dict[str, Any]] = []

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, **kwargs})
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    _checkout(tmp_path, "app")
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("SCAN_TEST_INHERITED", "kept")

    scan_workspace(tmp_path, days=30)

    # The user's config, then the origin (no config file here), history and status.
    assert sorted(call["args"][3] for call in calls) == ["config", "log", "remote", "status"]
    for call in calls:
        assert call["stdin"] == subprocess.DEVNULL
        assert call["timeout"] == 5
        assert call["env"][GIT_TERMINAL_PROMPT_ENV] == "0"
        assert call["env"][GIT_OPTIONAL_LOCKS_ENV] == "0"
        assert call["env"]["SCAN_TEST_INHERITED"] == "kept"


def test_cancel_between_repositories_stops_the_scan_and_renders_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for index in range(3):
        _checkout(tmp_path, f"r{index}", used_at=1_000.0 + index)
    console = _TurnConsole()
    measured: list[str] = []

    def read_status(checkout: Path) -> None:
        measured.append(checkout.name)
        console.cancel_requested = True  # ESC while the first repository is read

    _without_git(monkeypatch, read_status)
    _git_calls_at_once(monkeypatch, 1)
    scope = ActionToolScope(session=None, console=console)
    context = AgentToolContext(
        resolved_integrations={}, resources={ACTION_TOOL_CONTEXT_RESOURCE_KEY: scope}
    )

    result = scan_local_git_workspace(root=str(tmp_path), context=context)

    assert measured == ["r2"]
    assert result["cancelled"] is True
    assert result["stop_reason"] == "cancelled"
    assert console.printed == []


def test_walk_checks_the_time_budget_before_every_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A huge or slow tree must not hold the scan past its budget.
    for name in ("a", "b", "c", "d"):
        (tmp_path / name / "nested").mkdir(parents=True)
    _checkout(tmp_path / "a" / "nested", "repo")
    clock = _Clock()
    listed: list[str] = []
    real_scandir = os.scandir

    def scandir(path: Any) -> Any:
        listed.append(str(path))
        clock.now += 1.0
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)

    snapshot = scan_workspace(tmp_path, budget_seconds=2.5, clock=clock)

    assert len(listed) == 3
    assert snapshot.stop_reason is ScanStop.TIME_BUDGET
    assert snapshot.repos == ()


def test_walk_polls_the_cancel_probe_at_most_every_50_ms(tmp_path: Path) -> None:
    # A scheduled run's probe re-reads the task store (milliseconds per call);
    # asking it before every folder would spend the whole budget on probes.
    for index in range(20):
        (tmp_path / f"d{index}").mkdir()
    probes: list[str] = []

    def should_stop() -> bool:
        probes.append("asked")
        return False

    snapshot = scan_workspace(tmp_path, should_stop=should_stop, clock=_Clock())

    # 21 folders listed while the clock stood still: asked once in the walk, once after it.
    assert len(probes) == 2
    assert snapshot.stop_reason is None


def test_time_budget_keeps_the_most_recently_used_repositories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Two git calls run at once. "stale" and "older" hang in git status and use up
    # the budget, so the pool is full and "oldest" never starts; the scan returns
    # the two most recently used checkouts without waiting for the hung calls.
    used = {
        "oldest": 1_000.0,
        "newest": 5_000.0,
        "stale": 3_000.0,
        "recent": 4_000.0,
        "older": 2_000.0,
    }
    for name, used_at in used.items():
        _checkout(tmp_path, name, used_at=used_at)
    clock = _Clock()
    release = threading.Event()
    started: list[str] = []
    finished: list[str] = []

    def read_status(checkout: Path) -> None:
        started.append(checkout.name)
        if checkout.name == "older":
            clock.now = 30.0  # past the 20 s budget, once both slots hang
        if checkout.name in ("stale", "older"):
            release.wait(timeout=10.0)
        finished.append(checkout.name)

    _without_git(monkeypatch, read_status)
    _git_calls_at_once(monkeypatch, 2)

    try:
        snapshot = scan_workspace(tmp_path, clock=clock)
        hung_at_return = {"stale", "older"} - set(finished)
    finally:
        release.set()

    assert sorted(repo.name for repo in snapshot.repos) == ["newest", "recent"]
    assert sorted(started) == ["newest", "older", "recent", "stale"]
    assert hung_at_return == {"stale", "older"}
    assert snapshot.stop_reason is ScanStop.TIME_BUDGET
    assert "time limit" in snapshot_text(snapshot)


def test_progress_lines_are_throttled_to_one_every_three_seconds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for index in range(10):
        _checkout(tmp_path, f"r{index}", used_at=1_000.0 + index)
    clock = _Clock()

    def read_status(_checkout: Path) -> None:
        clock.now += 1.0

    _without_git(monkeypatch, read_status)
    _git_calls_at_once(monkeypatch, 1)
    lines: list[str] = []

    scan_workspace(tmp_path, on_progress=lines.append, clock=clock)

    assert lines == [
        "Counting recent commits: 3 of 10 repositories done",
        "Counting recent commits: 6 of 10 repositories done",
        "Counting recent commits: 9 of 10 repositories done",
    ]


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("darwin", _ALWAYS_SKIPPED + _MACOS_PROTECTED), ("linux", _ALWAYS_SKIPPED)],
)
def test_privacy_protected_folders_are_skipped_only_on_macos(
    platform: str, expected: tuple[str, ...]
) -> None:
    home = Path("/home/u")

    skips = default_skip_paths(home, cwd=home, platform=platform)

    assert skips == {home / name for name in expected}


def test_tool_skips_default_folders_unless_started_inside_or_named_as_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    _checkout(home / "code", "app")
    notes = _checkout(home / "Documents", "notes")
    _checkout(home / "Music", "band-site")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("tools.system.workspace_git_scan.tool.sys.platform", "darwin")
    monkeypatch.chdir(tmp_path)
    _without_git(monkeypatch)

    default = scan_local_git_workspace()
    named = scan_local_git_workspace(root=str(home / "Documents"))
    monkeypatch.chdir(notes)
    started_inside = scan_local_git_workspace()

    assert [repo["name"] for repo in default["repos"]] == ["app"]
    assert default["skipped"] == [str(home / "Documents"), str(home / "Music")]
    assert "Skipped Documents (macOS privacy-protected)" in default["response_text"]
    assert [repo["name"] for repo in named["repos"]] == ["notes"]
    assert sorted(repo["name"] for repo in started_inside["repos"]) == ["app", "notes"]


def test_tool_skips_default_folders_whichever_spelling_reaches_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Skip paths are matched against the walk's own spelling of each folder, so a
    # symlinked home or root must not let the walk into a skipped folder.
    home = tmp_path / "home"
    _checkout(home / "code", "app")
    band_site = _checkout(home / "Music", "band-site")
    link = tmp_path / "home-link"
    link.symlink_to(home, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    _without_git(monkeypatch)

    monkeypatch.setenv("HOME", str(home))
    root_through_link = scan_local_git_workspace(root=str(link))
    monkeypatch.setenv("HOME", str(link))
    home_through_link = scan_local_git_workspace(root=str(home))
    monkeypatch.chdir(link / "Music" / band_site.name)
    started_inside = scan_local_git_workspace()

    assert [repo["name"] for repo in root_through_link["repos"]] == ["app"]
    assert [repo["name"] for repo in home_through_link["repos"]] == ["app"]
    assert sorted(repo["name"] for repo in started_inside["repos"]) == ["app", "band-site"]


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
        return getattr(scan_local_git_workspace, REGISTERED_TOOL_ATTR)

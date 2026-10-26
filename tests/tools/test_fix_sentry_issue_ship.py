"""Tests for the Sentry issue-fix *shipping* path (branch + commit + push + PR).

Covers the git primitives against a real temp repo with a local bare remote, the
PR call with a mocked GitHub client, the ship orchestration, and the tool's
``open_pr`` wiring including every safety refusal (ship disabled, missing token,
protected branch, no changes, PR failure).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from integrations.coding_agent import CodingResult
from integrations.git import changed_paths, current_branch, file_fingerprints
from integrations.git.errors import COMMIT_FAILED, PUSH_FAILED, GitCommandError
from integrations.github.client import GitHubApiError
from integrations.github.pull_requests import (
    GitHubPullRequestError,
    PullRequest,
    open_pull_request,
)
from tools.cross_vendor.fix_sentry_issue import fix_sentry_issue
from tools.cross_vendor.fix_sentry_issue.context import IssueContext
from tools.cross_vendor.fix_sentry_issue.errors import (
    ERR_GITHUB_TOKEN,
    ERR_NO_CHANGES,
    ERR_PR_FAILED,
    ERR_SHIP_DISABLED,
    FixIssueError,
)
from tools.cross_vendor.fix_sentry_issue.ship import build_branch_name, ship_fix

_URL = "https://acme.sentry.io/issues/12345/"


# --------------------------------------------------------------------------- #
# git helpers
# --------------------------------------------------------------------------- #
def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


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


def _success_result() -> CodingResult:
    return CodingResult(
        success=True,
        summary="Guard the None case in process_event.",
        changed_files=["app/handlers.py"],
        diff="diff --git a/app/handlers.py b/app/handlers.py\n",
        returncode=0,
    )


# --------------------------------------------------------------------------- #
# pr.open_pull_request
# --------------------------------------------------------------------------- #
_SCOPE = "integrations.github.pull_requests.detect_git_remote_repo_scope"
_CLIENT = "integrations.github.pull_requests.GitHubRestClient"


@patch(_SCOPE, return_value=("acme", "app"))
def test_open_pull_request_builds_payload(mock_scope: MagicMock) -> None:
    client = MagicMock()
    client.request.return_value = {"html_url": "https://github.com/acme/app/pull/7", "number": 7}
    with patch(_CLIENT, return_value=client):
        pr = open_pull_request(
            "/ws",
            head_branch="opensre/sentry-fix-1-x",
            base_branch="main",
            title="fix: resolve Sentry issue 1",
            body="body",
            github_token="tok",
        )
    assert pr == PullRequest(url="https://github.com/acme/app/pull/7", number=7)
    method, path = client.request.call_args.args
    body = client.request.call_args.kwargs["body"]
    assert (method, path) == ("POST", "repos/acme/app/pulls")
    assert body["head"] == "opensre/sentry-fix-1-x"
    assert body["base"] == "main"


def test_open_pull_request_requires_token() -> None:
    with patch.dict("os.environ", {}, clear=True), pytest.raises(GitHubPullRequestError) as exc:
        open_pull_request(
            "/ws", head_branch="b", base_branch="main", title="t", body="b", github_token=""
        )
    assert exc.value.kind == ERR_GITHUB_TOKEN


@patch(_SCOPE, return_value=None)
def test_open_pull_request_unknown_repo_scope(_mock_scope: MagicMock) -> None:
    with pytest.raises(GitHubPullRequestError) as exc:
        open_pull_request(
            "/ws", head_branch="b", base_branch="main", title="t", body="b", github_token="tok"
        )
    assert exc.value.kind == "repo_scope_unresolved"


@patch(_SCOPE, return_value=("acme", "app"))
def test_open_pull_request_maps_api_error(_mock_scope: MagicMock) -> None:
    client = MagicMock()
    client.request.side_effect = GitHubApiError("validation failed", status_code=422)
    with patch(_CLIENT, return_value=client), pytest.raises(GitHubPullRequestError) as exc:
        open_pull_request(
            "/ws", head_branch="b", base_branch="main", title="t", body="b", github_token="tok"
        )
    assert exc.value.kind == ERR_PR_FAILED


# --------------------------------------------------------------------------- #
# ship.ship_fix
# --------------------------------------------------------------------------- #
def test_build_branch_name_is_namespaced(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    name = build_branch_name(str(work), "12345")
    assert name.startswith("opensre/sentry-fix-12345-")
    assert name != "main"


def test_build_branch_name_slugs_weird_ids(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    assert build_branch_name(str(work), "a/b c!").startswith("opensre/sentry-fix-a-b-c-")


def test_ship_fix_no_changes_raises(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)  # clean tree
    with pytest.raises(FixIssueError) as exc:
        ship_fix(str(work), issue_id="12345", sentry_url=_URL, result=_success_result())
    assert exc.value.kind == ERR_NO_CHANGES


def test_ship_fix_fails_clearly_when_base_branch_unresolved(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    (work / "app").mkdir()
    (work / "app" / "handlers.py").write_text("x = 1\n")

    # Base branch can't be resolved (offline + no origin/HEAD) -> fail, don't guess.
    with (
        patch("tools.cross_vendor.fix_sentry_issue.ship.default_branch", return_value=""),
        pytest.raises(FixIssueError) as exc,
    ):
        ship_fix(str(work), issue_id="12345", sentry_url=_URL, result=_success_result())

    assert exc.value.kind == ERR_PR_FAILED
    assert exc.value.branch_name is None  # nothing was created
    assert current_branch(str(work)) == "main"  # still on base, no fix branch


def test_ship_fix_full_roundtrip(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    (work / "app").mkdir()
    (work / "app" / "handlers.py").write_text("x = 1\n")

    with patch(
        "tools.cross_vendor.fix_sentry_issue.ship.open_pull_request",
        return_value=PullRequest(url="https://github.com/acme/app/pull/9", number=9),
    ) as mock_pr:
        ship = ship_fix(str(work), issue_id="12345", sentry_url=_URL, result=_success_result())

    assert ship.branch_name.startswith("opensre/sentry-fix-12345-")
    assert ship.pr.number == 9
    # PR opened from the new branch into the base branch.
    assert mock_pr.call_args.kwargs["base_branch"] == "main"
    assert mock_pr.call_args.kwargs["head_branch"] == ship.branch_name
    # The fix was committed onto the feature branch, not main.
    assert current_branch(str(work)) == ship.branch_name
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=work, capture_output=True, text=True
    ).stdout
    assert "12345" in log


def test_ship_fix_tags_branch_on_push_failure(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    (work / "app").mkdir()
    (work / "app" / "handlers.py").write_text("x = 1\n")

    # Push fails after the branch is created and the fix is committed.
    with (
        patch(
            "tools.cross_vendor.fix_sentry_issue.ship.push_branch",
            side_effect=GitCommandError(PUSH_FAILED, "remote rejected"),
        ),
        pytest.raises(FixIssueError) as exc,
    ):
        ship_fix(str(work), issue_id="12345", sentry_url=_URL, result=_success_result())

    # The error carries the branch that holds the committed fix, for manual recovery.
    assert exc.value.branch_name is not None
    assert exc.value.branch_name.startswith("opensre/sentry-fix-12345-")
    # The commit really exists on that branch.
    assert current_branch(str(work)) == exc.value.branch_name
    committed = subprocess.run(
        ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
        cwd=work,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert committed == ["app/handlers.py"]


def test_ship_fix_tags_branch_on_commit_failure(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    (work / "app").mkdir()
    (work / "app" / "handlers.py").write_text("x = 1\n")

    # Commit fails after the branch is created (e.g. hook rejection / missing identity).
    with (
        patch(
            "tools.cross_vendor.fix_sentry_issue.ship.commit_paths",
            side_effect=GitCommandError(COMMIT_FAILED, "git commit failed"),
        ),
        pytest.raises(FixIssueError) as exc,
    ):
        ship_fix(str(work), issue_id="12345", sentry_url=_URL, result=_success_result())

    # Even though nothing was committed, the workspace switched to the new branch,
    # so the error must surface it for recovery instead of reporting None.
    assert exc.value.branch_name is not None
    assert exc.value.branch_name.startswith("opensre/sentry-fix-12345-")
    assert current_branch(str(work)) == exc.value.branch_name


def test_ship_fix_retries_cleanly_after_commit_failure(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    (work / "app").mkdir()
    (work / "app" / "handlers.py").write_text("x = 1\n")

    with (
        patch(
            "tools.cross_vendor.fix_sentry_issue.ship.commit_paths",
            side_effect=GitCommandError(COMMIT_FAILED, "git commit failed"),
        ),
        pytest.raises(FixIssueError) as exc,
    ):
        ship_fix(str(work), issue_id="12345", sentry_url=_URL, result=_success_result())
    branch = exc.value.branch_name

    # Retry with nothing else changed: HEAD hasn't moved (the failed commit never
    # landed), so the branch name resolves identically. It must resume on the
    # branch it's already sitting on rather than fail with "branch already exists".
    with patch(
        "tools.cross_vendor.fix_sentry_issue.ship.open_pull_request",
        return_value=PullRequest(url="https://github.com/acme/app/pull/9", number=9),
    ):
        ship = ship_fix(str(work), issue_id="12345", sentry_url=_URL, result=_success_result())

    assert ship.branch_name == branch
    assert current_branch(str(work)) == branch


def test_ship_fix_branches_off_base_not_workspaces_current_branch(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    # The operator has some other local branch checked out, with a commit that
    # never made it into origin/main.
    _git(work, "checkout", "-b", "unrelated-feature")
    (work / "unrelated.txt").write_text("not part of the fix\n")
    _git(work, "add", "unrelated.txt")
    _git(work, "commit", "-m", "unrelated work in progress")

    # Pi's fix, left uncommitted while the workspace is still on that foreign branch.
    (work / "app").mkdir()
    (work / "app" / "handlers.py").write_text("x = 1\n")

    with patch(
        "tools.cross_vendor.fix_sentry_issue.ship.open_pull_request",
        return_value=PullRequest(url="https://github.com/acme/app/pull/9", number=9),
    ):
        ship = ship_fix(str(work), issue_id="12345", sentry_url=_URL, result=_success_result())

    # The fix branch must contain only the fix commit on top of main -- never the
    # unrelated commit from whatever branch the workspace happened to be on.
    commits_ahead_of_base = (
        subprocess.run(
            ["git", "log", "--oneline", f"main..{ship.branch_name}"],
            cwd=work,
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .splitlines()
    )
    assert len(commits_ahead_of_base) == 1
    assert "unrelated" not in commits_ahead_of_base[0]


def _baseline(work: Path) -> dict[str, str]:
    return file_fingerprints(str(work), changed_paths(str(work)))


def test_ship_fix_commits_only_pi_files_not_unrelated_wip(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    # Unrelated developer WIP present BEFORE the Pi run -> captured in the baseline.
    (work / "wip.txt").write_text("do not ship me\n")
    baseline = _baseline(work)
    assert list(baseline) == ["wip.txt"]

    # Pi's fix, created during the run (wip.txt is left untouched).
    (work / "app").mkdir()
    (work / "app" / "handlers.py").write_text("x = 1\n")

    result = CodingResult(
        success=True, summary="s", changed_files=["app/handlers.py"], diff="", returncode=0
    )
    with patch(
        "tools.cross_vendor.fix_sentry_issue.ship.open_pull_request",
        return_value=PullRequest(url="https://github.com/acme/app/pull/9", number=9),
    ):
        ship = ship_fix(
            str(work), issue_id="12345", sentry_url=_URL, result=result, baseline=baseline
        )

    committed = subprocess.run(
        ["git", "show", "--name-only", "--pretty=format:", ship.branch_name],
        cwd=work,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert committed == ["app/handlers.py"]  # untouched wip.txt was NOT swept in
    assert (
        "wip.txt"
        in subprocess.run(
            ["git", "status", "--porcelain"], cwd=work, capture_output=True, text=True
        ).stdout
    )


def test_ship_fix_includes_pi_edit_to_already_dirty_file(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    # README.md is tracked; a developer has uncommitted WIP in it before the run.
    (work / "README.md").write_text("dev wip\n")
    baseline = _baseline(work)
    assert list(baseline) == ["README.md"]

    # Pi ALSO edits the same file — its change must NOT be dropped just because the
    # file was already dirty.
    (work / "README.md").write_text("dev wip + pi fix\n")

    result = CodingResult(
        success=True, summary="s", changed_files=["README.md"], diff="", returncode=0
    )
    with patch(
        "tools.cross_vendor.fix_sentry_issue.ship.open_pull_request",
        return_value=PullRequest(url="https://github.com/acme/app/pull/9", number=9),
    ):
        ship = ship_fix(
            str(work), issue_id="12345", sentry_url=_URL, result=result, baseline=baseline
        )

    committed = subprocess.run(
        ["git", "show", "--name-only", "--pretty=format:", ship.branch_name],
        cwd=work,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert committed == ["README.md"]  # Pi's edit to the dirty file is shipped


def test_ship_fix_no_new_changes_over_baseline_raises(tmp_path: Path) -> None:
    work = _init_repo(tmp_path)
    # Everything dirty was already there before Pi and left untouched (all baseline).
    (work / "wip.txt").write_text("pre-existing\n")
    baseline = _baseline(work)

    with pytest.raises(FixIssueError) as exc:
        ship_fix(
            str(work),
            issue_id="12345",
            sentry_url=_URL,
            result=_success_result(),
            baseline=baseline,
        )
    assert exc.value.kind == ERR_NO_CHANGES


# --------------------------------------------------------------------------- #
# tool run() with open_pr
# --------------------------------------------------------------------------- #
_CTX = IssueContext(issue_id="12345", task="Sentry issue task")
_TOOL_GATHER = "tools.cross_vendor.fix_sentry_issue.gather_issue_context"
_TOOL_CLI = "tools.cross_vendor.fix_sentry_issue.ensure_cli_ready"
_TOOL_RUNFIX = "tools.cross_vendor.fix_sentry_issue.run_fix"
_TOOL_SHIP = "tools.cross_vendor.fix_sentry_issue.run_ship"
_TOOL_PRE = "tools.cross_vendor.fix_sentry_issue.pre_pi_changes"


@patch(_TOOL_RUNFIX)
@patch(_TOOL_CLI)
@patch(_TOOL_GATHER, return_value=_CTX)
def test_run_open_pr_disabled_fails_fast(
    _gather: MagicMock, _cli: MagicMock, mock_runfix: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PI_ISSUE_FIX_ENABLED", "1")
    monkeypatch.delenv("PI_ISSUE_FIX_SHIP_ENABLED", raising=False)
    out = fix_sentry_issue.run(sentry_url=_URL, open_pr=True)
    assert out["error_kind"] == ERR_SHIP_DISABLED
    mock_runfix.assert_not_called()  # never spend a Pi run if we can't ship
    # The issue is already resolved, so its id must survive the ship-gate failure.
    assert out["issue_id"] == _CTX.issue_id
    # Early-gate failures must still carry the full, stable output shape so
    # callers can read these keys without KeyError.
    for key in ("branch_name", "pr_url", "pr_number", "summary", "changed_files", "diff"):
        assert key in out
    assert out["pr_url"] is None and out["branch_name"] is None and out["pr_number"] is None


@patch(_TOOL_RUNFIX)
@patch(_TOOL_CLI)
@patch(_TOOL_GATHER, return_value=_CTX)
def test_run_open_pr_missing_token_fails_fast(
    _gather: MagicMock, _cli: MagicMock, mock_runfix: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PI_ISSUE_FIX_ENABLED", "1")
    monkeypatch.setenv("PI_ISSUE_FIX_SHIP_ENABLED", "1")
    # Clear every fallback resolve_github_token consults, including the MCP env.
    monkeypatch.delenv("GITHUB_MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    out = fix_sentry_issue.run(sentry_url=_URL, open_pr=True)
    assert out["error_kind"] == ERR_GITHUB_TOKEN
    assert out["issue_id"] == _CTX.issue_id  # resolved id preserved on ship-gate failure
    mock_runfix.assert_not_called()


@patch(_TOOL_PRE, return_value={"pre_existing_wip.txt": "deadbeef"})
@patch(_TOOL_SHIP)
@patch(_TOOL_RUNFIX, return_value=_success_result())
@patch("tools.cross_vendor.fix_sentry_issue.ensure_ship_ready")
@patch(_TOOL_CLI)
@patch(_TOOL_GATHER, return_value=_CTX)
def test_run_open_pr_success_returns_pr(
    _gather: MagicMock,
    _cli: MagicMock,
    _ship_ready: MagicMock,
    _runfix: MagicMock,
    mock_ship: MagicMock,
    _pre: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.cross_vendor.fix_sentry_issue.ship import ShipResult

    mock_ship.return_value = ShipResult(
        branch_name="opensre/sentry-fix-12345-abc",
        pr=PullRequest(url="https://github.com/acme/app/pull/9", number=9),
    )
    monkeypatch.setenv("PI_ISSUE_FIX_ENABLED", "1")
    monkeypatch.setenv("PI_ISSUE_FIX_SHIP_ENABLED", "1")
    out = fix_sentry_issue.run(sentry_url=_URL, open_pr=True)
    assert out["success"] is True
    assert out["error_kind"] is None
    assert out["pr_url"] == "https://github.com/acme/app/pull/9"
    assert out["pr_number"] == 9
    assert out["branch_name"] == "opensre/sentry-fix-12345-abc"
    # The pre-Pi fingerprint snapshot is threaded to the ship step as the baseline.
    assert mock_ship.call_args.kwargs["baseline"] == {"pre_existing_wip.txt": "deadbeef"}


@patch(_TOOL_PRE, return_value=())
@patch(
    _TOOL_SHIP,
    side_effect=FixIssueError(
        "push_failed", "remote rejected", branch_name="opensre/sentry-fix-12345-xyz"
    ),
)
@patch(_TOOL_RUNFIX, return_value=_success_result())
@patch("tools.cross_vendor.fix_sentry_issue.ensure_ship_ready")
@patch(_TOOL_CLI)
@patch(_TOOL_GATHER, return_value=_CTX)
def test_run_open_pr_ship_failure_keeps_diff_and_branch(
    _gather: MagicMock,
    _cli: MagicMock,
    _ship_ready: MagicMock,
    _runfix: MagicMock,
    _ship: MagicMock,
    _pre: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PI_ISSUE_FIX_ENABLED", "1")
    monkeypatch.setenv("PI_ISSUE_FIX_SHIP_ENABLED", "1")
    out = fix_sentry_issue.run(sentry_url=_URL, open_pr=True)
    assert out["success"] is False
    assert out["error_kind"] == "push_failed"
    assert out["pr_url"] is None
    assert "diff --git" in out["diff"]  # the fix is preserved for manual shipping
    assert out["changed_files"] == ["app/handlers.py"]
    # A post-commit failure surfaces the branch so the user can push it manually.
    assert out["branch_name"] == "opensre/sentry-fix-12345-xyz"


@patch(_TOOL_SHIP)
@patch(_TOOL_RUNFIX, return_value=_success_result())
@patch(_TOOL_CLI)
@patch(_TOOL_GATHER, return_value=_CTX)
def test_run_without_open_pr_never_ships(
    _gather: MagicMock,
    _cli: MagicMock,
    _runfix: MagicMock,
    mock_ship: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PI_ISSUE_FIX_ENABLED", "1")
    out = fix_sentry_issue.run(sentry_url=_URL)
    assert out["success"] is True
    assert out["pr_url"] is None
    assert out["branch_name"] is None
    mock_ship.assert_not_called()

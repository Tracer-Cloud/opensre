"""Unattended GitHub security fix: dedup against open fix PRs and worktree isolation."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from integrations.coding_agent import CodingResult
from integrations.github.client import GitHubRestClient
from integrations.github.pull_requests import PullRequest
from integrations.github.tools.security_fix import unattended
from integrations.github.tools.security_fix.context import (
    SecurityAlertContext,
    gather_security_alert_context,
)
from integrations.github.tools.security_fix.errors import (
    ERR_FIX_ALREADY_OPEN,
    GitHubSecurityFixError,
)
from integrations.github.tools.security_fix.ship import parse_fix_branch_name
from integrations.github.tools.security_fix.unattended import (
    open_fix_alert_keys,
    run_unattended_security_fix,
)

_CTX = SecurityAlertContext(
    owner="acme",
    repo="app",
    alert_type="dependabot",
    number=12,
    summary="Django vulnerable",
    url="https://github.com/acme/app/security/dependabot/12",
    task="Fix Dependabot alert.",
)


class _FakeGitHubClient:
    def __init__(self, pulls: list[dict[str, Any]]) -> None:
        self.pulls = pulls
        self.paths: list[str] = []

    def paginate(self, path: str, **_kwargs: Any) -> list[dict[str, Any]]:
        self.paths.append(path)
        return list(self.pulls)


def _dependabot_alert(number: int, severity: str) -> dict[str, Any]:
    return {
        "number": number,
        "html_url": f"https://github.com/acme/app/security/dependabot/{number}",
        "dependency": {"package": {"ecosystem": "pip", "name": f"pkg{number}"}},
        "security_vulnerability": {"severity": severity},
        "security_advisory": {"summary": f"issue {number}"},
    }


def test_open_fix_alert_keys_reads_only_opensre_fix_branches() -> None:
    client = _FakeGitHubClient(
        [
            {"head": {"ref": "opensre/github-security-fix-dependabot-12-abc123"}},
            {"head": {"ref": "opensre/github-security-fix-code_quality-7-deadbeef"}},
            {"head": {"ref": "opensre/ci-fix-main-abc-def"}},
            {"head": {"ref": "feature/unrelated"}},
            {"head": None},
        ]
    )

    keys = open_fix_alert_keys(client, owner="acme", repo="app")  # type: ignore[arg-type]

    assert keys == frozenset({("dependabot", 12), ("code_quality", 7)})
    assert client.paths == ["/repos/acme/app/pulls"]
    assert parse_fix_branch_name("opensre/github-security-fix-dependabot-12") is None


def test_auto_selection_skips_findings_with_an_open_fix_pr() -> None:
    """A 30-minute cadence must move on to the next finding, not re-fix the same one."""
    alerts = [_dependabot_alert(1, "low"), _dependabot_alert(2, "high")]

    def fake_paginate(_self: GitHubRestClient, path: str, **_kwargs: Any) -> list[dict[str, Any]]:
        return alerts if path.endswith("/dependabot/alerts") else []

    with patch.object(GitHubRestClient, "paginate", fake_paginate):
        ctx = gather_security_alert_context(
            owner="acme", repo="app", github_token="tok", exclude={("dependabot", 2)}
        )
        assert ctx.number == 1

        with pytest.raises(GitHubSecurityFixError) as excinfo:
            gather_security_alert_context(
                owner="acme",
                repo="app",
                github_token="tok",
                exclude={("dependabot", 1), ("dependabot", 2)},
            )
    assert excinfo.value.kind == ERR_FIX_ALREADY_OPEN
    assert "no new PR was created" in excinfo.value.message


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _seed_repository(tmp_path: Path) -> tuple[Path, Path]:
    """Return ``(origin_bare, clone)`` with one commit on ``main`` and origin/HEAD set."""
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.email", "test@example.com")
    _git(seed, "config", "user.name", "Test")
    (seed / "requirements.txt").write_text("django==3.2.0\n", encoding="utf-8")
    _git(seed, "add", "requirements.txt")
    _git(seed, "commit", "-q", "-m", "seed")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(seed, "push", "-q", str(origin), "main")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    _git(clone, "config", "user.email", "test@example.com")
    _git(clone, "config", "user.name", "Test")
    return origin, clone


def _worktree_dirs(clone: Path) -> list[Path]:
    return sorted(clone.parent.glob(".opensre-security-fix-*"))


def test_unattended_fix_ships_from_a_linked_worktree_and_leaves_the_checkout_alone(
    tmp_path: Path,
) -> None:
    origin, clone = _seed_repository(tmp_path)
    # A developer is mid-edit on an unrelated branch in the configured checkout.
    _git(clone, "checkout", "-q", "-b", "wip")
    (clone / "notes.md").write_text("uncommitted work\n", encoding="utf-8")
    fixed_in: list[str] = []

    def fake_run_fix(
        _ctx: SecurityAlertContext, workspace: str, _model: str | None
    ) -> CodingResult:
        fixed_in.append(workspace)
        (Path(workspace) / "requirements.txt").write_text("django==3.2.25\n", encoding="utf-8")
        return CodingResult(
            success=True, summary="Bump Django.", changed_files=["requirements.txt"]
        )

    with (
        patch.object(unattended, "ensure_workspace_ready"),
        patch.object(unattended, "verify_coding_agent", return_value=(True, "")),
        patch.object(unattended, "gather_security_alert_context", return_value=_CTX) as gather,
        patch.object(unattended, "run_fix", side_effect=fake_run_fix),
        patch(
            "integrations.github.tools.security_fix.ship.open_pull_request",
            return_value=PullRequest(url="https://github.com/acme/app/pull/9", number=9),
        ) as open_pr,
    ):
        result = run_unattended_security_fix(
            owner="acme",
            repo="app",
            workspace=str(clone),
            github_token="tok",
            client=_FakeGitHubClient(
                [{"head": {"ref": "opensre/github-security-fix-dependabot-3-000000"}}]
            ),  # type: ignore[arg-type]
        )

    assert result["success"] is True
    assert result["pr_url"] == "https://github.com/acme/app/pull/9"
    assert gather.call_args.kwargs["exclude"] == frozenset({("dependabot", 3)})
    # The fix ran in a sibling linked worktree, not in the developer's checkout.
    assert len(fixed_in) == 1
    assert Path(fixed_in[0]).parent == clone.parent
    assert Path(fixed_in[0]).name.startswith(".opensre-security-fix-")
    assert fixed_in[0] != str(clone)
    # The checkout keeps its branch and its uncommitted work.
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD") == "wip"
    assert (clone / "notes.md").read_text(encoding="utf-8") == "uncommitted work\n"
    assert (clone / "requirements.txt").read_text(encoding="utf-8") == "django==3.2.0\n"
    # The fix branch reached origin with exactly the fix, based on origin/main.
    branch = result["branch_name"]
    assert parse_fix_branch_name(branch) == ("dependabot", 12)
    remote_branches = _git(origin, "branch", "--list", branch)
    assert branch in remote_branches
    assert _git(origin, "show", f"{branch}:requirements.txt") == "django==3.2.25"
    assert _git(origin, "rev-parse", f"{branch}^") == _git(origin, "rev-parse", "main")
    assert open_pr.call_args.kwargs["head_branch"] == branch
    assert open_pr.call_args.kwargs["base_branch"] == "main"
    # Nothing is left behind for the next tick: no worktree, no local fix branch.
    assert _worktree_dirs(clone) == []
    assert _git(clone, "branch", "--list", branch) == ""


def test_unattended_fix_removes_the_worktree_when_no_patch_is_produced(tmp_path: Path) -> None:
    _origin, clone = _seed_repository(tmp_path)

    with (
        patch.object(unattended, "ensure_workspace_ready"),
        patch.object(unattended, "verify_coding_agent", return_value=(True, "")),
        patch.object(unattended, "gather_security_alert_context", return_value=_CTX),
        patch.object(
            unattended,
            "run_fix",
            return_value=CodingResult(success=False, summary="", error="agent gave up"),
        ),
        patch("integrations.github.tools.security_fix.ship.open_pull_request") as open_pr,
    ):
        result = run_unattended_security_fix(
            owner="acme",
            repo="app",
            workspace=str(clone),
            github_token="tok",
            client=_FakeGitHubClient([]),  # type: ignore[arg-type]
        )

    assert result["success"] is False
    assert result["error"] == "agent gave up"
    assert result["pr_url"] is None
    open_pr.assert_not_called()
    assert _worktree_dirs(clone) == []
    assert _git(clone, "branch", "--list", "opensre/*") == ""
    assert _git(clone, "status", "--porcelain") == ""

"""Direct pushes of a GitHub security fix onto the default branch, against a real bare remote."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from integrations.coding_agent import CodingResult
from integrations.git import MERGE_FAILED
from integrations.github.pull_requests import PullRequest
from integrations.github.tools.security_fix.context import SecurityAlertContext
from integrations.github.tools.security_fix.errors import GitHubSecurityFixError
from integrations.github.tools.security_fix.ship import ShipResult, push_security_fix

_CTX = SecurityAlertContext(
    owner="acme",
    repo="app",
    alert_type="code_scanning",
    number=7,
    summary="Unused global variable",
    url="https://github.com/acme/app/security/code-scanning/7",
    task="Fix code scanning alert.",
)
_RESULT = CodingResult(success=True, summary="Removed the unused global.", changed_files=["app.py"])
_RULE_HOOK = """#!/bin/sh
while read old new ref; do
  if [ "$ref" = "refs/heads/main" ]; then
    echo "error: GH013: Repository rule violations found for refs/heads/main." >&2
    echo "- Changes must be made through a pull request." >&2
    exit 1
  fi
done
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _identify(work: Path) -> None:
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")


def _remote_with_clone(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A bare ``origin`` on ``main``, a teammate's checkout, and the run-owned fix clone."""
    bare = tmp_path / "remote.git"
    teammate = tmp_path / "teammate"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(bare))
    _git(tmp_path, "init", "-b", "main", str(teammate))
    _identify(teammate)
    (teammate / "app.py").write_text("UNUSED = 1\nx = 1\n")
    _git(teammate, "add", "-A")
    _git(teammate, "commit", "-m", "init")
    _git(teammate, "remote", "add", "origin", str(bare))
    _git(teammate, "push", "-u", "origin", "main")
    _git(tmp_path, "clone", str(bare), str(clone))
    _identify(clone)
    return bare, teammate, clone


def _teammate_pushes(teammate: Path, path: str, content: str) -> str:
    (teammate / path).write_text(content)
    _git(teammate, "add", "-A")
    _git(teammate, "commit", "-m", f"teammate edits {path}")
    _git(teammate, "push", "origin", "main")
    return _git(teammate, "rev-parse", "HEAD")


def _push(clone: Path) -> ShipResult:
    with patch("integrations.github.tools.security_fix.ship.resolve_github_token", return_value=""):
        return push_security_fix(str(clone), ctx=_CTX, result=_RESULT)


def test_direct_push_lands_the_fix_on_the_default_branch(tmp_path: Path) -> None:
    bare, _teammate, clone = _remote_with_clone(tmp_path)
    (clone / "app.py").write_text("x = 1\n")

    ship = _push(clone)

    assert ship.pr is None
    assert ship.branch_name == "main"
    assert _git(bare, "rev-parse", "main") == ship.commit_sha
    assert _git(bare, "show", "main:app.py") == "x = 1"


def test_direct_push_rebases_once_when_main_moved_during_the_fix(tmp_path: Path) -> None:
    bare, teammate, clone = _remote_with_clone(tmp_path)
    teammate_head = _teammate_pushes(teammate, "other.py", "y = 2\n")
    (clone / "app.py").write_text("x = 1\n")

    ship = _push(clone)

    assert _git(bare, "rev-parse", "main") == ship.commit_sha
    assert _git(bare, "rev-parse", "main^") == teammate_head
    assert _git(bare, "show", "main:app.py") == "x = 1"


def test_direct_push_stops_when_the_fix_conflicts_with_the_moved_branch(tmp_path: Path) -> None:
    bare, teammate, clone = _remote_with_clone(tmp_path)
    teammate_head = _teammate_pushes(teammate, "app.py", "UNUSED = 3\nx = 3\n")
    (clone / "app.py").write_text("x = 1\n")

    with pytest.raises(GitHubSecurityFixError) as raised:
        _push(clone)

    assert raised.value.kind == MERGE_FAILED
    assert _git(bare, "rev-parse", "main") == teammate_head
    assert not (clone / ".git" / "rebase-merge").exists()


@pytest.mark.skipif(os.name == "nt", reason="the pre-receive hook needs a POSIX shell")
def test_a_branch_rule_ships_the_fix_as_a_pr_and_leaves_main_alone(tmp_path: Path) -> None:
    bare, _teammate, clone = _remote_with_clone(tmp_path)
    main_before = _git(bare, "rev-parse", "main")
    hook = bare / "hooks" / "pre-receive"
    hook.write_text(_RULE_HOOK)
    hook.chmod(0o755)
    (clone / "app.py").write_text("x = 1\n")
    opened = PullRequest(url="https://github.com/acme/app/pull/9", number=9)

    with patch(
        "integrations.github.tools.security_fix.ship.open_pull_request", return_value=opened
    ) as open_pr:
        ship = _push(clone)

    assert ship.pr == opened
    assert "branch rule requires a pull request" in ship.pr_reason
    assert ship.branch_name.startswith("opensre/github-security-fix-code_scanning-7-")
    assert _git(bare, "rev-parse", "main") == main_before
    assert _git(bare, "show", f"{ship.branch_name}:app.py") == "x = 1"
    kwargs = open_pr.call_args.kwargs
    assert (kwargs["head_branch"], kwargs["base_branch"]) == (ship.branch_name, "main")

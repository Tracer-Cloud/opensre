"""The local repository insights read real git history and never let commit text or names out."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from config.constants.local_insights import LocalInsightKind
from core.agent_harness.tools.tool_context import (
    ACTION_TOOL_CONTEXT_RESOURCE_KEY,
    ActionToolScope,
)
from core.tool.contracts import REGISTERED_TOOL_ATTR, AgentToolContext
from surfaces.interactive_shell.session import Session
from tests.tools.conftest import BaseToolContract
from tools.system.local_repo_insights import tool as tool_module
from tools.system.local_repo_insights.analysis import analyze_repositories
from tools.system.local_repo_insights.metrics import CiRun
from tools.system.local_repo_insights.tool import analyze_local_repositories

_ME = "me@example.com"
_WORKFLOW = """\
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/upload-artifact@v3
      - uses: actions/setup-python@0b93645e9fea7318ecaed2b359559ac225c90a2b
  lint:
    uses: acme/shared/.github/workflows/lint.yml@main
"""
# Subjects and addresses that must never leave the tool.
_PRIVATE = (
    "fix typo in the payments service",
    "Pair with Claude Dupont",
    "ci: try again",
    _ME,
    "mate@example.com",
    "claude.dupont@example.com",
)


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No global or system git config: no hooks path, signing or identity from the machine."""
    config = tmp_path / "gitconfig"
    config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env={**os.environ, **(env or {})},
    )


def _commit(
    repo: Path,
    subject: str,
    *,
    at: datetime,
    files: tuple[str, ...],
    email: str = _ME,
    committer: str = "",
    trailer: str = "",
) -> None:
    for path in files:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        # Workflow edits keep the file valid YAML; every edit changes the file.
        base = _WORKFLOW if path.startswith(".github/workflows/") else ""
        target.write_text(f"{base}# {at.isoformat()}\n")
    _git(repo, "add", "-A")
    message = f"{subject}\n\n{trailer}" if trailer else subject
    stamp = at.isoformat()
    _git(
        repo,
        "commit",
        "-q",
        "--no-verify",
        "-m",
        message,
        env={
            "GIT_AUTHOR_NAME": "Someone",
            "GIT_AUTHOR_EMAIL": email,
            "GIT_AUTHOR_DATE": stamp,
            "GIT_COMMITTER_NAME": "Someone",
            "GIT_COMMITTER_EMAIL": committer or email,
            "GIT_COMMITTER_DATE": stamp,
        },
    )


def _payments_repo(root: Path) -> tuple[Path, datetime]:
    """Ten commits by the user that exercise every counting rule, plus two that must not count."""
    repo = root / "payments"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", _ME)
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / ".pre-commit-config.yaml").write_text("repos: []\n")
    start = (datetime.now(UTC) - timedelta(days=3)).replace(hour=10, minute=0, second=0)
    minutes = timedelta(minutes=1)
    _commit(repo, "Add service", at=start, files=("src/app.py", "tests/test_app.py"))
    # A fix to the file just changed, 10 minutes later: a follow-up fix.
    _commit(
        repo, "fix typo in the payments service", at=start + 10 * minutes, files=("src/app.py",)
    )
    # A fix to another file, hours later: new work, not a follow-up.
    _commit(repo, "fix flaky retry", at=start + 180 * minutes, files=("src/other.py",))
    # Three CI-config commits within 20 minutes; the last two are follow-up fixes too.
    _commit(repo, "ci: add workflow", at=start + 300 * minutes, files=(".github/workflows/ci.yml",))
    _commit(repo, "ci: try again", at=start + 310 * minutes, files=(".github/workflows/ci.yml",))
    _commit(repo, "ci: fix cache", at=start + 320 * minutes, files=(".github/workflows/ci.yml",))
    _commit(
        repo,
        "Add feature",
        at=start + timedelta(days=1),
        files=("src/feature.py",),
        trailer="Co-authored-by: Claude <noreply@anthropic.com>",
    )
    # A person named Claude is not an AI agent.
    _commit(
        repo,
        "Pair with Claude Dupont",
        at=start + timedelta(days=1, hours=2),
        files=("src/pair.py",),
        trailer="Co-authored-by: Claude Dupont <claude.dupont@example.com>",
    )
    _commit(
        repo,
        "Docs",
        at=start + timedelta(days=1, hours=3),
        files=("README.md",),
        trailer="Co-authored-by: Codex <codex@openai.com>",
    )
    _commit(
        repo,
        "More docs",
        at=start + timedelta(days=1, hours=4),
        files=("docs/guide.md",),
        trailer="Co-authored-by: Cursor Agent <cursoragent@cursor.com>",
    )
    # A teammate's revert counts for the repository, not for the user.
    _commit(
        repo,
        'Revert "Add feature"',
        at=start + timedelta(days=1, hours=5),
        files=("src/feature.py",),
        email="mate@example.com",
    )
    # GitHub's merge button repeats work already counted: left out entirely.
    _commit(
        repo,
        "Squash merged on GitHub",
        at=start + timedelta(days=1, hours=6),
        files=("src/merged.py",),
        committer="noreply@github.com",
    )
    return repo, start


def test_every_counting_rule_and_the_insight_order(tmp_path: Path) -> None:
    repo, start = _payments_repo(tmp_path)

    analysis = analyze_repositories(days=30, paths=[str(repo)])

    [metrics] = analysis.repos
    assert metrics.name == "payments"
    assert (metrics.commits, metrics.own_commits) == (11, 10)
    assert metrics.follow_up_fixes == 3
    assert metrics.reverts == 1
    assert metrics.ai_coauthored == 3
    assert dict(metrics.agents) == {"Claude": 1, "Codex": 1, "Cursor": 1}
    assert metrics.ci_runs == (
        CiRun(
            commits=3,
            minutes=20,
            day=(start + timedelta(minutes=300)).date().isoformat(),
            files=(".github/workflows/ci.yml",),
        ),
    )
    audit = metrics.workflows
    assert audit is not None
    assert (audit.jobs, audit.jobs_without_timeout) == (1, 1)
    assert (audit.action_refs, audit.unpinned_action_refs) == (4, 3)
    assert audit.retired_action_refs == ("actions/upload-artifact@v3",)
    assert audit.runs_without_concurrency == 1
    assert (metrics.hygiene.hook_framework, metrics.hygiene.hook_installed) == ("pre-commit", False)
    assert [insight.kind for insight in analysis.insights] == [
        LocalInsightKind.RETIRED_ACTIONS,
        LocalInsightKind.FOLLOW_UP_FIXES,
        LocalInsightKind.CI_TRIAL_AND_ERROR,
        LocalInsightKind.AI_PAIRING,
        LocalInsightKind.WORKFLOW_HYGIENE,
        LocalInsightKind.HOOKS_NOT_INSTALLED,
    ]
    follow_up = analysis.insights[1]
    assert follow_up.fact == (
        "3 of your 10 commits (30%) fixed files you had changed less than an hour earlier "
        "in payments."
    )
    # A summary is what telemetry records: it never names the repository.
    assert all("payments" not in insight.summary for insight in analysis.insights)


def test_the_tool_result_telemetry_and_value_notes_carry_no_commit_text_or_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo, _start = _payments_repo(tmp_path)
    recorded: list[dict[str, Any]] = []

    def capture(**properties: Any) -> None:
        recorded.append(properties)

    monkeypatch.setattr(tool_module, "capture_local_repositories_analyzed", capture)
    session = Session()
    context = AgentToolContext(
        resolved_integrations={},
        resources={
            ACTION_TOOL_CONTEXT_RESOURCE_KEY: ActionToolScope(session=session, console=None)
        },
    )

    result = analyze_local_repositories(
        reason="github_failed", github_error="TLS Untrusted!", paths=[str(repo)], context=context
    )

    assert result["success"] is True
    assert result["reason"] == "github_failed"
    assert [item["label"] for item in result["insights"]][:2] == [
        "Retired actions",
        "Follow-up fixes",
    ]
    serialized = json.dumps(result)
    assert not [text for text in _PRIVATE if text in serialized]
    [event] = recorded
    assert event["reason"] == "github_failed"
    assert event["github_error"] == "tls_untrusted"
    assert event["outcome"] == "ok"
    assert (event["repositories"], event["own_commits"]) == (1, 10)
    assert event["ai_coauthored_share"] == 30
    assert event["insight_kinds"][0] == "retired_actions"
    telemetry = json.dumps(event)
    assert "payments" not in telemetry
    assert str(tmp_path) not in telemetry
    assert not [text for text in _PRIVATE if text in telemetry]
    notes = session.skill_value_notes
    assert notes["Follow-up fixes"][0] == "follow_up_fixes"
    assert not [summary for _kind, summary in notes.values() if "payments" in summary]


def test_without_paths_the_workspace_is_scanned_and_a_missing_repository_is_named(
    tmp_path: Path,
) -> None:
    repo, _start = _payments_repo(tmp_path)
    empty = tmp_path / "scratch"
    empty.mkdir()
    _git(empty, "init", "-q", "-b", "main")

    analysis = analyze_repositories(days=30, root=str(tmp_path), repository="acme/billing")

    # The repository without commits in the window is not read; the active one is.
    assert [metrics.name for metrics in analysis.repos] == [repo.name]
    assert analysis.notices == (
        "No local checkout of acme/billing was found, so these insights cover your other "
        "repositories.",
    )


class TestAnalyzeLocalRepositoriesContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return getattr(analyze_local_repositories, REGISTERED_TOOL_ATTR)

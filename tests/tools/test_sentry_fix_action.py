"""Tests for the interactive-shell 'fix Sentry issue' action tool (intake routing).

Covers gating (only offered when opted in), dispatch to ``fix_sentry_issue`` with
the right args, result rendering, and the action-context wrapper. The action LLM's
intent selection is a live concern exercised via ReplDriver, not here.
"""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

import tools.interactive_shell.actions.sentry_fix as sentry_fix
from core.agent_harness.tools.tool_context import (
    ACTION_TOOL_CONTEXT_RESOURCE_KEY,
    ActionToolScope,
)
from core.tool.contracts import AgentToolContext
from tools.interactive_shell.shared import ExecutionPolicyResult
from tools.interactive_shell.subprocess_presenter import HeadlessSubprocessPresenter

_TOOL_RUN = "tools.interactive_shell.actions.sentry_fix.fix_sentry_issue.run"
_URL = "https://acme.sentry.io/issues/12345/"


class _GatePresenter(HeadlessSubprocessPresenter):
    """Headless presenter that records the execution-policy gate."""

    def __init__(self, session: Any, console: Any, *, allowed: bool = True) -> None:
        super().__init__(session, console)
        self.allowed = allowed
        self.gate_calls: list[tuple[ExecutionPolicyResult, str]] = []

    def execution_allowed(
        self,
        policy: ExecutionPolicyResult,
        *,
        action_summary: str,
    ) -> bool:
        self.gate_calls.append((policy, action_summary))
        return self.allowed


def _ctx(*, allowed: bool = True) -> tuple[ActionToolScope, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, width=200)
    session = MagicMock()
    return (
        ActionToolScope(
            session=session,
            console=console,
            subprocess_presenter=_GatePresenter(session, console, allowed=allowed),
        ),
        buf,
    )


# --------------------------------------------------------------------------- #
# execute_sentry_fix_tool
# --------------------------------------------------------------------------- #
def test_empty_url_is_not_handled() -> None:
    ctx, _ = _ctx()
    assert sentry_fix.execute_sentry_fix_tool({"sentry_url": "   "}, ctx) is False


@patch(_TOOL_RUN)
def test_dispatches_with_open_pr_and_renders_pr(mock_run: MagicMock) -> None:
    mock_run.return_value = {
        "success": True,
        "error_kind": None,
        "issue_id": "12345",
        "summary": "Guarded the None case.",
        "changed_files": ["app/handlers.py"],
        "pr_url": "https://github.com/acme/app/pull/9",
        "branch_name": "opensre/sentry-fix-12345-abc",
    }
    ctx, buf = _ctx()
    handled = sentry_fix.execute_sentry_fix_tool({"sentry_url": _URL, "open_pr": True}, ctx)

    assert handled is True
    mock_run.assert_called_once_with(sentry_url=_URL, open_pr=True)
    out = buf.getvalue()
    assert "opening a pull request" in out
    assert "https://github.com/acme/app/pull/9" in out
    assert "app/handlers.py" in out


@patch(_TOOL_RUN)
def test_dispatches_diff_only_by_default(mock_run: MagicMock) -> None:
    mock_run.return_value = {
        "success": True,
        "issue_id": "12345",
        "summary": "s",
        "changed_files": ["a.py"],
        "pr_url": None,
        "branch_name": None,
    }
    ctx, buf = _ctx()
    sentry_fix.execute_sentry_fix_tool({"sentry_url": _URL}, ctx)

    mock_run.assert_called_once_with(sentry_url=_URL, open_pr=False)
    assert "no PR requested" in buf.getvalue()


@patch(_TOOL_RUN)
def test_renders_error(mock_run: MagicMock) -> None:
    mock_run.return_value = {
        "success": False,
        "error_kind": "push_failed",
        "error": "remote rejected",
        "changed_files": ["a.py"],
    }
    ctx, buf = _ctx()
    sentry_fix.execute_sentry_fix_tool({"sentry_url": _URL, "open_pr": True}, ctx)

    out = buf.getvalue()
    assert "Could not fix" in out
    assert "push_failed" in out
    assert "still in your working tree" in out


@patch(_TOOL_RUN)
def test_renders_push_failure_says_committed_needs_push(mock_run: MagicMock) -> None:
    mock_run.return_value = {
        "success": False,
        "error_kind": "push_failed",
        "error": "remote rejected",
        "changed_files": ["a.py"],
        "branch_name": "opensre/sentry-fix-12345-abc",
    }
    ctx, buf = _ctx()
    sentry_fix.execute_sentry_fix_tool({"sentry_url": _URL, "open_pr": True}, ctx)

    out = buf.getvalue()
    assert "opensre/sentry-fix-12345-abc" in out
    assert "committed on branch" in out
    assert "push it and open the PR manually" in out


@patch(_TOOL_RUN)
def test_renders_commit_failure_says_not_committed(mock_run: MagicMock) -> None:
    mock_run.return_value = {
        "success": False,
        "error_kind": "commit_failed",
        "error": "git commit failed",
        "changed_files": ["a.py"],
        "branch_name": "opensre/sentry-fix-12345-abc",
    }
    ctx, buf = _ctx()
    sentry_fix.execute_sentry_fix_tool({"sentry_url": _URL, "open_pr": True}, ctx)

    out = buf.getvalue()
    # Nothing was committed, so the user must commit first — not just push.
    assert "not committed" in out
    assert "commit them, push, and open the PR manually" in out


@patch(_TOOL_RUN)
def test_renders_pr_failed_recovery_says_already_pushed(mock_run: MagicMock) -> None:
    mock_run.return_value = {
        "success": False,
        "error_kind": "pr_failed",
        "error": "422 validation failed",
        "changed_files": ["a.py"],
        "branch_name": "opensre/sentry-fix-12345-abc",
    }
    ctx, buf = _ctx()
    sentry_fix.execute_sentry_fix_tool({"sentry_url": _URL, "open_pr": True}, ctx)

    out = buf.getvalue()
    # The branch is already pushed, so guidance is "open the PR", not "push it".
    assert "pushed to branch" in out
    assert "open the PR manually" in out
    assert "push and open" not in out


@patch(_TOOL_RUN)
def test_refused_approval_does_not_run_the_fix(mock_run: MagicMock) -> None:
    ctx, _ = _ctx(allowed=False)
    handled = sentry_fix.execute_sentry_fix_tool({"sentry_url": _URL, "open_pr": True}, ctx)

    assert handled is True
    mock_run.assert_not_called()
    presenter = ctx.subprocess_presenter
    assert isinstance(presenter, _GatePresenter)
    assert len(presenter.gate_calls) == 1
    policy, summary = presenter.gate_calls[0]
    assert policy.tool_type == "sentry_issue_fix"
    assert "open a pull request" in summary


# --------------------------------------------------------------------------- #
# run_sentry_fix (action-context wrapper)
# --------------------------------------------------------------------------- #
@patch(_TOOL_RUN, return_value={"success": True, "issue_id": "1", "pr_url": None})
def test_run_sentry_fix_uses_action_context(mock_run: MagicMock) -> None:
    action_ctx, _ = _ctx()
    agent_ctx = AgentToolContext(
        resolved_integrations={},
        resources={ACTION_TOOL_CONTEXT_RESOURCE_KEY: action_ctx},
    )
    result = sentry_fix.run_sentry_fix(sentry_url=_URL, context=agent_ctx, open_pr=False)
    assert result["ok"] is True
    mock_run.assert_called_once_with(sentry_url=_URL, open_pr=False)


# --------------------------------------------------------------------------- #
# gating + registry
# --------------------------------------------------------------------------- #
def test_is_available_follows_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = sentry_fix.fix_sentry_issue_start_tool
    monkeypatch.delenv("PI_ISSUE_FIX_ENABLED", raising=False)
    assert tool.is_available({}) is False
    monkeypatch.setenv("PI_ISSUE_FIX_ENABLED", "1")
    assert tool.is_available({}) is True


def test_registered_as_action_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.registry import get_registered_tool_map

    monkeypatch.setenv("PI_ISSUE_FIX_ENABLED", "1")
    action = get_registered_tool_map("action")
    assert "fix_sentry_issue_start" in action
    tool = action["fix_sentry_issue_start"]
    assert tool.side_effect_level == "mutating"
    assert tool.input_schema["required"] == ["sentry_url"]
    # Not offered on the investigation/chat surfaces (that's the underlying tool).
    assert "fix_sentry_issue_start" not in get_registered_tool_map("investigation")

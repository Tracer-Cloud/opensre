"""Failed work-tool outcomes keep the ReAct turn open."""

from __future__ import annotations

from typing import Any

from config.constants.slash_commands import QUEUED_COMMAND_KEY
from core.agent_harness.turns.work_outcome import (
    ExecutedToolOutcome,
    last_work_classified,
    last_work_needs_setup,
    last_work_ok,
    last_work_tool_failed,
    tap_executed_tool_outcomes,
)
from core.events import ToolExecutionEndEvent, ToolExecutionStartEvent
from core.llm.types import ToolCall
from core.tool.execution import ToolExecutionResult
from core.tool_framework.utils import tool_unavailable

_SETUP = "/integrations setup github"
_TOKEN_REQUIRED = "A GitHub token is required to read the Actions history of acme/app."


def _outcome(
    name: str,
    *,
    is_error: bool = False,
    details: object = None,
    arguments: dict[str, object] | None = None,
) -> ExecutedToolOutcome:
    return ExecutedToolOutcome(
        name=name,
        arguments=dict(arguments or {}),
        is_error=is_error,
        details=details,
    )


def _ran(
    name: str,
    details: dict[str, Any],
    *,
    is_error: bool = False,
    metadata: dict[str, Any] | None = None,
) -> tuple[ToolCall, ToolExecutionResult]:
    """One loop result as the goal observation carries it: raw details, not the compat payload."""
    return (
        ToolCall(id=f"call-{name}", name=name, input={}),
        ToolExecutionResult(
            content=str(details.get("error") or "ok"),
            details=details,
            is_error=is_error,
            metadata=dict(metadata or {}),
        ),
    )


def test_the_last_work_tool_needs_setup_whatever_non_work_or_skipped_calls_follow() -> None:
    """A wizard queued after the failure, or a call skipped by it, is not the last work."""
    needs_setup = _ran(
        "analyze_github_ci_reliability",
        tool_unavailable("github", _TOKEN_REQUIRED, setup_command=_SETUP),
        is_error=True,
    )
    queued_wizard = _ran("slash_invoke", {"ok": True, QUEUED_COMMAND_KEY: _SETUP})
    skipped = _ran(
        "shell_run",
        {"error": "Not run: slash_invoke ended the turn"},
        is_error=True,
        metadata={"skipped": True},
    )

    assert last_work_needs_setup([needs_setup, queued_wizard, skipped]) is True
    # A later work tool that ran is the last work, setup or not.
    assert last_work_needs_setup([needs_setup, _ran("shell_run", {"ok": True})]) is False


def test_an_unavailable_tool_without_a_named_setup_is_an_ordinary_failure() -> None:
    unavailable = _ran(
        "analyze_github_ci_reliability",
        tool_unavailable("github", _TOKEN_REQUIRED, setup_command=" "),
        is_error=True,
    )

    assert last_work_needs_setup([unavailable]) is False


def test_failed_curl_is_not_successful_work() -> None:
    failed = _outcome(
        "shell_run",
        details={"ok": False, "command": "curl https://api.github.com/repos/o/r", "exit_code": 1},
    )
    assert last_work_tool_failed([failed]) is True


def test_later_successful_work_recovers_a_failed_curl() -> None:
    failed = _outcome(
        "shell_run",
        details={"ok": False, "command": "curl https://api.github.com/repos/o/r", "exit_code": 1},
    )
    recovered = _outcome(
        "get_github_repository",
        details={"ok": True, "stargazers_count": 42, "available": True},
    )
    assert last_work_tool_failed([failed, recovered]) is False


def test_plan_write_after_a_failed_curl_does_not_hide_the_failure() -> None:
    failed = _outcome("shell_run", details={"ok": False, "exit_code": 1})
    plan = _outcome("update_plan", details={"ok": True})
    assert last_work_tool_failed([failed, plan]) is True


def test_bookkeeping_only_is_not_a_failed_work_stop() -> None:
    plan = _outcome("update_plan", details={"ok": True})
    assert last_work_tool_failed([plan]) is False
    assert last_work_ok([plan]) is None
    assert last_work_tool_failed([]) is False


def test_classified_repair_outcome_is_a_finished_report() -> None:
    details = {
        "success": False,
        "error_kind": "pr_not_open",
        "work_outcome": {"status": "blocked", "error_kind": "pr_not_open"},
    }
    assert last_work_tool_failed([_outcome("fix_github_pr_ci", details=details)]) is True
    assert last_work_classified([_ran("fix_github_pr_ci", details)]) is True
    unfinished = _ran("shell_run", {"ok": False, "exit_code": 1})
    assert last_work_classified([unfinished]) is False


def test_a_classified_failure_that_also_reports_an_error_is_a_finished_report() -> None:
    """Regression: a failed call's compat payload keeps only ``{"error": text}``, so a read or
    repair that classified its block and also reported an error lost its ``work_outcome``,
    and the turn host made the agent retry what could not succeed."""
    blocked = _ran(
        "analyze_github_ci_reliability",
        {
            "error": "GitHub is rate-limiting this token.",
            "work_outcome": {"status": "blocked", "error_kind": "rate_limited"},
        },
        is_error=True,
    )

    assert last_work_classified([blocked]) is True


def test_execution_error_counts_as_failed_work() -> None:
    assert last_work_tool_failed([_outcome("shell_run", is_error=True, details={"error": "boom"})])


def test_tap_records_payload_from_tool_end_events() -> None:
    recorded: list[ExecutedToolOutcome] = []
    callback = tap_executed_tool_outcomes(None, recorded)
    callback(
        ToolExecutionStartEvent(
            tool_call_id="1",
            tool_name="shell_run",
            args={"command": "curl"},
            iteration=0,
        )
    )
    callback(
        ToolExecutionEndEvent(
            tool_call_id="1",
            tool_name="shell_run",
            args={"command": "curl"},
            result={"ok": False, "exit_code": 1},
            is_error=False,
            iteration=0,
        )
    )
    assert len(recorded) == 1
    assert recorded[0].name == "shell_run"
    assert recorded[0].details == {"ok": False, "exit_code": 1}
    assert last_work_tool_failed(recorded) is True


def test_tap_ignores_calls_skipped_after_the_turn_ended() -> None:
    """A skipped call never ran; its error reply must not read as failed work."""
    recorded: list[ExecutedToolOutcome] = []
    callback = tap_executed_tool_outcomes(None, recorded)
    callback(
        ToolExecutionEndEvent(
            tool_call_id="s-1",
            tool_name="shell_run",
            args={"command": "curl"},
            result={"error": "Not run: ask_user_choice ended the turn"},
            is_error=True,
            iteration=0,
            data={"skipped": True},
        )
    )
    assert recorded == []
    assert last_work_tool_failed(recorded) is False

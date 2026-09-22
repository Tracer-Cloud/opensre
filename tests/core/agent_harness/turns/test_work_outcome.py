"""Failed work-tool outcomes keep the ReAct turn open."""

from __future__ import annotations

from core.agent_harness.turns.work_outcome import (
    ExecutedToolOutcome,
    last_work_classified,
    last_work_ok,
    last_work_tool_failed,
    tap_executed_tool_outcomes,
)
from core.events import ToolExecutionEndEvent, ToolExecutionStartEvent


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
    blocked = _outcome(
        "fix_github_pr_ci",
        details={
            "success": False,
            "error_kind": "pr_not_open",
            "work_outcome": {"status": "blocked", "error_kind": "pr_not_open"},
        },
    )
    assert last_work_tool_failed([blocked]) is True
    assert last_work_classified([blocked]) is True
    unfinished = _outcome("shell_run", details={"ok": False, "exit_code": 1})
    assert last_work_classified([unfinished]) is False


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

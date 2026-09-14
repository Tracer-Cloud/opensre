"""The second work tool of a turn is refused while no plan is open."""

from __future__ import annotations

from types import SimpleNamespace

from core.agent_harness.task_plan.plan import parse_task_plan
from core.agent_harness.task_plan.required import PLAN_REQUIRED_REASON
from core.agent_harness.turns.plan_hooks import with_task_plan_hooks
from core.domain.types.tools import ToolRole
from core.llm.types import ToolCall
from core.tool.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from surfaces.interactive_shell.session import Session


def _request(name: str, *, role: ToolRole = ToolRole.ACTION) -> ToolExecutionRequest:
    call = ToolCall(id=f"call-{name}", name=name, input={})
    return ToolExecutionRequest(
        tool_call=call,
        tool=SimpleNamespace(role=role),  # type: ignore[arg-type]
        arguments={},
        source="test",
        resolved_integrations={},
    )


def _plan(*statuses: str) -> object:
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "List the workflow files", "status": statuses[0]},
                {"step": "Count the jobs in each", "status": statuses[1]},
            ]
        }
    )
    assert error is None
    return plan


def _returned(
    hooks: ToolExecutionHooks, name: str, *, ok: bool = True, payload_ok: bool = True
) -> None:
    hooks.after_tool_call(
        _request(name),
        ToolExecutionResult(content="out", details={"ok": payload_ok}, is_error=not ok),
    )


def test_the_second_work_tool_is_refused_until_a_plan_is_stored() -> None:
    # Arrange: a fresh turn in which one work tool has returned.
    session = Session()
    hooks = with_task_plan_hooks(None, session)
    _returned(hooks, "shell_run")

    # Act
    decision = hooks.before_tool_call(_request("shell_run"))

    # Assert: refused, with the fix named.
    assert decision is not None and decision.blocked is True
    assert decision.reason == PLAN_REQUIRED_REASON
    assert decision.metadata == {"plan_required": True}


def test_the_first_work_tool_and_non_work_calls_are_never_refused() -> None:
    # Arrange
    session = Session()
    hooks = with_task_plan_hooks(None, session)

    # Act / Assert: nothing has run yet, so a lookup is not a workload.
    assert hooks.before_tool_call(_request("shell_run")) is None
    _returned(hooks, "shell_run")
    # Bookkeeping, the hand-off to the user, and slash commands are not work calls.
    assert hooks.before_tool_call(_request("update_plan", role=ToolRole.BOOKKEEPING)) is None
    assert hooks.before_tool_call(_request("memory_remember", role=ToolRole.BOOKKEEPING)) is None
    assert hooks.before_tool_call(_request("ask_user_choice", role=ToolRole.TURN_ENDING)) is None
    assert hooks.before_tool_call(_request("slash_invoke")) is None


def test_slash_commands_and_failed_calls_do_not_count_as_work() -> None:
    """The live run: `rg` was not installed, and the retry with `grep` was refused."""
    # Arrange: two shell commands, one tool error, and one command that failed to start.
    session = Session()
    hooks = with_task_plan_hooks(None, session)
    _returned(hooks, "slash_invoke")
    _returned(hooks, "slash_invoke")
    _returned(hooks, "shell_run", ok=False)
    _returned(hooks, "shell_run", payload_ok=False)
    hooks.after_tool_call(
        _request("fix_github_pr_ci"),
        ToolExecutionResult(
            content="blocked", details={"success": False, "error_kind": "repo_mismatch"}
        ),
    )

    # Act / Assert: the first work tool is still allowed.
    assert hooks.before_tool_call(_request("shell_run")) is None


def test_an_open_plan_lets_work_continue_but_a_settled_one_does_not() -> None:
    # Arrange: one work tool already returned this turn.
    session = Session()
    hooks = with_task_plan_hooks(None, session)
    _returned(hooks, "shell_run")

    # Act / Assert
    session.task_plan = _plan("completed", "in_progress")
    assert hooks.before_tool_call(_request("shell_run")) is None
    session.task_plan = _plan("completed", "completed")
    decision = hooks.before_tool_call(_request("shell_run"))
    assert decision is not None and decision.blocked is True


def test_a_base_refusal_wins_over_the_plan_rule() -> None:
    # Arrange: the wrapped hook already refuses the call for its own reason.
    session = Session()
    base = ToolExecutionHooks(
        before_tool_call=lambda _request: BeforeToolCallResult(blocked=True, reason="duplicate")
    )
    hooks = with_task_plan_hooks(base, session)
    _returned(hooks, "shell_run")

    # Act
    decision = hooks.before_tool_call(_request("shell_run"))

    # Assert
    assert decision is not None and decision.reason == "duplicate"

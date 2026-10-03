"""A lone update_plan that starts or advances a step is refused before it is stored."""

from __future__ import annotations

from typing import Any

from core.agent_harness.task_plan.plan import TaskPlan, parse_task_plan
from core.agent_harness.task_plan.solo_advance import (
    SOLO_PLAN_ADVANCE_REASON,
    solo_plan_advance_reason,
)
from core.agent_harness.turns.plan_hooks import with_task_plan_hooks
from core.domain.types.tools import ToolRole
from core.llm.types import ToolCall
from core.tool.contracts import AgentTool, AgentToolContext
from core.tool.execution import execute_tool_calls
from surfaces.interactive_shell.session import Session

_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "plan": {"type": "array"},
        "plan_only": {"type": "boolean"},
        "explanation": {"type": "string"},
    },
    "required": ["plan"],
    "additionalProperties": False,
}
_LOOSE_SCHEMA: dict[str, Any] = {"type": "object", "additionalProperties": True}


def _steps(*statuses: str) -> list[dict[str, str]]:
    return [
        {"step": f"Step {index + 1} does one thing", "status": status}
        for index, status in enumerate(statuses)
    ]


def _payload(*statuses: str, plan_only: bool = False, explanation: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"plan": _steps(*statuses)}
    if plan_only:
        payload["plan_only"] = True
    if explanation:
        payload["explanation"] = explanation
    return payload


def _call(name: str, payload: dict[str, Any], *, call_id: str | None = None) -> ToolCall:
    return ToolCall(id=call_id or f"call-{name}", name=name, input=payload)


def _stored(*statuses: str) -> TaskPlan:
    plan, error = parse_task_plan(_payload(*statuses))
    assert error is None and plan is not None
    return plan


def _reason(
    payload: dict[str, Any],
    *extra: ToolCall,
    prior: TaskPlan | None = None,
) -> str | None:
    return solo_plan_advance_reason((_call("update_plan", payload), *extra), prior=prior)


def test_a_solo_in_progress_write_is_refused_and_not_stored() -> None:
    ran: list[str] = []

    def execute(_args: dict[str, Any], _ctx: AgentToolContext) -> dict[str, Any]:
        ran.append("update_plan")
        return {"ok": True}

    session = Session()
    hooks = with_task_plan_hooks(None, session)
    tool = AgentTool(
        name="update_plan",
        description="Record the plan",
        input_schema=_PLAN_SCHEMA,
        execute=execute,
        role=ToolRole.BOOKKEEPING,
    )

    results = execute_tool_calls(
        [_call("update_plan", _payload("in_progress", "pending"))],
        [tool],
        {},
        hooks=hooks,
    )

    assert ran == []
    assert session.task_plan is None
    assert results[0].is_error is True
    assert results[0].content == SOLO_PLAN_ADVANCE_REASON
    assert results[0].metadata["solo_plan_advance"] is True


def test_an_in_progress_write_beside_an_action_runs() -> None:
    ran: list[str] = []

    def write_plan(_args: dict[str, Any], _ctx: AgentToolContext) -> dict[str, Any]:
        ran.append("update_plan")
        return {"ok": True}

    def run_shell(_args: dict[str, Any], _ctx: AgentToolContext) -> dict[str, Any]:
        ran.append("shell_run")
        return {"ok": True}

    session = Session()
    hooks = with_task_plan_hooks(None, session)
    tools = [
        AgentTool(
            name="update_plan",
            description="Record the plan",
            input_schema=_PLAN_SCHEMA,
            execute=write_plan,
            role=ToolRole.BOOKKEEPING,
        ),
        AgentTool(
            name="shell_run",
            description="Run a command",
            input_schema=_LOOSE_SCHEMA,
            execute=run_shell,
        ),
    ]

    results = execute_tool_calls(
        [
            _call("update_plan", _payload("in_progress", "pending")),
            _call("shell_run", {"command": "true"}, call_id="call-shell"),
        ],
        tools,
        {},
        hooks=hooks,
    )

    assert ran == ["update_plan", "shell_run"]
    assert all(result.is_error is False for result in results)


def test_plan_only_and_an_all_pending_checklist_are_allowed() -> None:
    assert _reason(_payload("in_progress", "pending", plan_only=True)) is None
    assert _reason(_payload("pending", "pending")) is None


def test_a_write_that_newly_blocks_a_step_is_allowed_on_its_own() -> None:
    blocked = _payload("blocked", "pending", explanation="The token is missing.")
    assert _reason(blocked) is None
    started = _payload("blocked", "in_progress", explanation="The token is missing.")
    assert _reason(started) is None


def test_a_settled_close_is_allowed_on_its_own() -> None:
    prior = _stored("in_progress", "pending")
    assert _reason(_payload("completed", "completed"), prior=prior) is None


def test_starting_or_completing_a_step_on_its_own_is_refused() -> None:
    assert _reason(_payload("in_progress", "pending")) == SOLO_PLAN_ADVANCE_REASON
    assert _reason(_payload("completed", "pending")) == SOLO_PLAN_ADVANCE_REASON
    prior = _stored("in_progress", "pending")
    assert _reason(_payload("in_progress", "pending"), prior=prior) == SOLO_PLAN_ADVANCE_REASON
    assert _reason(_payload("completed", "pending"), prior=prior) == SOLO_PLAN_ADVANCE_REASON


def test_a_second_call_or_an_invalid_plan_is_not_this_refusal() -> None:
    assert (
        _reason(
            _payload("in_progress", "pending"),
            _call("memory_remember", {"text": "noted"}, call_id="call-memory"),
        )
        is None
    )
    assert _reason({"plan": []}) is None

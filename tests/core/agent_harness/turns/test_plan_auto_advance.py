"""The host advances the plan only on the next step's work, and only when earned."""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console

from core.agent_harness.session.pending_choice import AskUserQuestion, format_ask_user_answers
from core.agent_harness.task_plan.plan import PlanStepStatus, TaskPlan, parse_task_plan
from core.agent_harness.tools.tool_context import ActionToolScope
from core.agent_harness.turns.plan_hooks import with_task_plan_hooks
from core.domain.types.tools import ToolRole
from core.llm.types import ToolCall
from core.tool.contracts import AgentTool, AgentToolContext
from core.tool.execution import ToolExecutionHooks, execute_tool_calls
from surfaces.interactive_shell.session import Session
from tools.interactive_shell.actions.update_plan import execute_update_plan_tool

_C = PlanStepStatus.COMPLETED
_IP = PlanStepStatus.IN_PROGRESS
_P = PlanStepStatus.PENDING
_B = PlanStepStatus.BLOCKED
_STEPS = ("Read the workflow files", "Count the jobs in each", "Report the totals")


def _work_tool(name: str, role: ToolRole = ToolRole.ACTION) -> AgentTool:
    return AgentTool(
        name=name,
        description=name,
        input_schema={"type": "object", "additionalProperties": True},
        execute=lambda _args, _ctx: {"ok": True},
        role=role,
    )


class _Turn:
    """One action turn's hooks over real tool execution."""

    def __init__(self, session: Session, message: str = "Count the CI jobs") -> None:
        self.session = session
        self.hooks: ToolExecutionHooks = with_task_plan_hooks(
            None, session, turn_user_message=message
        )
        scope = ActionToolScope(
            session=session, console=Console(file=io.StringIO()), turn_user_message=message
        )

        def write_plan(args: dict[str, Any], _ctx: AgentToolContext) -> dict[str, Any]:
            return execute_update_plan_tool(args, scope)

        self.tools = [
            AgentTool(
                name="update_plan",
                description="plan",
                input_schema={"type": "object", "additionalProperties": True},
                execute=write_plan,
                role=ToolRole.BOOKKEEPING,
            ),
            _work_tool("shell_run"),
            _work_tool("slash_invoke"),
            _work_tool("memory_remember", ToolRole.BOOKKEEPING),
        ]

    def batch(self, *calls: tuple[str, dict[str, Any]]) -> None:
        results = execute_tool_calls(
            [ToolCall(id=f"c{i}", name=n, input=a) for i, (n, a) in enumerate(calls)],
            self.tools,
            {},
            hooks=self.hooks,
        )
        assert not any(result.is_error for result in results)

    def write(self, *statuses: str) -> None:
        plan = [
            {"step": step, "status": status} for step, status in zip(_STEPS, statuses, strict=True)
        ]
        self.batch(("update_plan", {"plan": plan, "explanation": "x"}))

    def statuses(self) -> list[PlanStepStatus]:
        assert self.session.task_plan is not None
        return [item.status for item in self.session.task_plan.steps]


_RUN = ("shell_run", {"command": "ls"})


def test_the_next_work_call_completes_an_earned_step_and_starts_the_next() -> None:
    turn = _Turn(Session())
    turn.write("in_progress", "pending", "pending")

    # The step's own tool runs under it: nothing to complete yet.
    turn.batch(_RUN)
    assert turn.statuses() == [_IP, _P, _P]

    # The next work call: step 1 returned work, so it closes and step 2 starts.
    turn.batch(_RUN)
    assert turn.statuses() == [_C, _IP, _P]


def test_slash_and_bookkeeping_calls_never_advance_and_update_plan_wins() -> None:
    turn = _Turn(Session())
    turn.write("in_progress", "pending", "pending")
    turn.batch(_RUN)

    turn.batch(("slash_invoke", {"command": "/status"}))
    turn.batch(("memory_remember", {"fact": "x"}))
    # The model's own write in the batch is the plan; the host stays out.
    turn.batch(("update_plan", {"plan": [{"step": s, "status": "pending"} for s in _STEPS]}), _RUN)
    assert turn.statuses() == [_IP, _P, _P]


def test_a_settling_completion_is_left_to_the_model_and_blocked_steps_stay() -> None:
    turn = _Turn(Session())
    turn.write("in_progress", "blocked", "pending")
    turn.batch(_RUN)

    turn.batch(_RUN)
    assert turn.statuses() == [_C, _B, _IP]

    # Step 3 returned work, but completing it would settle the plan.
    turn.batch(_RUN)
    turn.batch(_RUN)
    assert turn.statuses() == [_C, _B, _IP]


def test_a_verifies_step_needs_its_own_tool_not_the_users_answer() -> None:
    answer = format_ask_user_answers(
        (AskUserQuestion(label="Go", title="Run the check?", options=("Yes", "No")),), ("Yes",)
    )
    session = Session()
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": _STEPS[0], "status": "completed"},
                {"step": _STEPS[1], "status": "in_progress", "verifies": True},
                {"step": _STEPS[2], "status": "pending"},
            ]
        }
    )
    assert error is None and isinstance(plan, TaskPlan)
    session.task_plan = plan
    turn = _Turn(session, message=answer)

    turn.batch(_RUN)
    assert turn.statuses() == [_C, _IP, _P]
    turn.batch(_RUN)
    assert turn.statuses() == [_C, _C, _IP]


def test_a_leftover_plan_under_a_new_request_is_not_advanced() -> None:
    session = Session()
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": s, "status": "in_progress" if i == 0 else "pending"}
                for i, s in enumerate(_STEPS)
            ]
        }
    )
    assert error is None and isinstance(plan, TaskPlan)
    session.task_plan = plan
    turn = _Turn(session, message="What time is it in UTC?")

    turn.batch(_RUN)
    turn.batch(_RUN)
    assert turn.statuses() == [_IP, _P, _P]

"""The host advances the plan only on the next step's work, and only when earned."""

from __future__ import annotations

import io
from dataclasses import replace
from typing import Any

from rich.console import Console

from core.agent_harness.session.pending_choice import (
    AskUserQuestion,
    format_ask_user_answers,
    question_key,
)
from core.agent_harness.task_plan.advance import advance_task_plan
from core.agent_harness.task_plan.ownership import session_answer_continues_plan
from core.agent_harness.task_plan.plan import (
    PlanStep,
    PlanStepStatus,
    TaskPlan,
    parse_task_plan,
    task_plan_from_payload,
    task_plan_to_payload,
)
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
_SKILL = "counting-ci-jobs"
_QUESTION = "Run the check?"


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
        # As the action driver does: decided once, before any tool runs.
        self.hooks: ToolExecutionHooks = with_task_plan_hooks(
            None,
            session,
            turn_user_message=message,
            answer_continues=session_answer_continues_plan(session, message),
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


def _stored(*statuses: str, owner: str | None = _SKILL, **flags: dict[str, bool]) -> TaskPlan:
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": step, "status": status, **flags.get(f"s{index}", {})}
                for index, (step, status) in enumerate(zip(_STEPS, statuses, strict=True))
            ],
            "explanation": "x",
        }
    )
    assert error is None and plan is not None
    return replace(plan, owner=owner)


def _answer(title: str = _QUESTION) -> str:
    question = AskUserQuestion(label="Go", title=title, options=("Yes", "No"))
    return format_ask_user_answers((question,), ("Yes",))


def _skill_session(plan: TaskPlan, *, asked_by: str = _SKILL) -> Session:
    session = Session()
    session.task_plan = plan
    session.active_skill = _SKILL
    session.skill_question_keys = {asked_by: {question_key(_QUESTION)}}
    return session


def test_a_write_records_the_active_skill_as_owner_and_it_survives_resume() -> None:
    session = Session()
    session.active_skill = _SKILL
    _Turn(session).write("in_progress", "pending", "pending")

    assert session.task_plan is not None and session.task_plan.owner == _SKILL
    restored = task_plan_from_payload(task_plan_to_payload(session.task_plan))
    assert restored is not None and restored.owner == _SKILL


def test_the_owner_skills_answer_advances_but_another_workflows_answer_does_not() -> None:
    # The plan's own skill asked: its answer settles step 1 on the next tool.
    own = _skill_session(_stored("in_progress", "pending", "pending"))
    _Turn(own, message=_answer()).batch(_RUN)
    assert [item.status for item in own.task_plan.steps] == [_C, _IP, _P]  # type: ignore[union-attr]

    # Another workflow asked the question: the plan stays where the model left it.
    other = _skill_session(_stored("in_progress", "pending", "pending"), asked_by="other-skill")
    turn = _Turn(other, message=_answer())
    turn.batch(_RUN)
    turn.batch(_RUN)
    assert turn.statuses() == [_IP, _P, _P]

    # A skill-less plan is never continued by an answer.
    loose = _skill_session(_stored("in_progress", "pending", "pending", owner=None))
    turn = _Turn(loose, message=_answer())
    turn.batch(_RUN)
    turn.batch(_RUN)
    assert turn.statuses() == [_IP, _P, _P]


def test_a_verifies_step_needs_its_own_tool_not_the_users_answer() -> None:
    session = _skill_session(_stored("completed", "in_progress", "pending", s1={"verifies": True}))
    turn = _Turn(session, message=_answer())

    turn.batch(_RUN)
    assert turn.statuses() == [_C, _IP, _P]
    turn.batch(_RUN)
    assert turn.statuses() == [_C, _C, _IP]


def test_a_leftover_plan_under_a_new_request_is_not_advanced() -> None:
    session = _skill_session(_stored("in_progress", "pending", "pending"))
    turn = _Turn(session, message="What time is it in UTC?")

    turn.batch(_RUN)
    turn.batch(_RUN)
    assert turn.statuses() == [_IP, _P, _P]


def test_the_next_step_is_after_the_active_one_never_behind_it() -> None:
    # A pending step behind the active one is not where the work goes next.
    stranded = TaskPlan(
        steps=(
            PlanStep(_STEPS[0], _P),
            PlanStep(_STEPS[1], _IP),
            PlanStep(_STEPS[2], _B),
        ),
        explanation="step 3 is blocked",
    )
    assert (
        advance_task_plan(stranded, tool_evidence=True, answer_evidence=False, reply_shown=False)
        is None
    )
    # With nothing in progress, the first pending step starts.
    idle = _stored("completed", "pending", "pending")
    started = advance_task_plan(idle, tool_evidence=False, answer_evidence=False, reply_shown=False)
    assert started is not None
    assert [item.status for item in started.steps] == [_C, _IP, _P]


def test_a_shown_reply_earns_exactly_one_deliverable_step() -> None:
    four = ("Compute", "Prepare the table", "Show the table", "Offer next steps")
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": four[0], "status": "in_progress"},
                {"step": four[1], "status": "pending", "deliverable": True},
                {"step": four[2], "status": "pending", "deliverable": True},
                {"step": four[3], "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None

    # The active step earned its own completion; the reply earns the next deliverable only.
    moved = advance_task_plan(plan, tool_evidence=True, answer_evidence=False, reply_shown=True)
    assert moved is not None
    assert [item.status for item in moved.steps] == [_C, _C, _IP, _P]

    # The second deliverable is active: a fresh reply earns it, and only it.
    again = advance_task_plan(moved, tool_evidence=False, answer_evidence=False, reply_shown=True)
    assert again is not None
    assert [item.status for item in again.steps] == [_C, _C, _C, _IP]

    # Without its own reply, the active deliverable stays.
    assert (
        advance_task_plan(moved, tool_evidence=False, answer_evidence=False, reply_shown=False)
        is None
    )

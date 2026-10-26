"""Tests for update_plan host policy after Ask User answers."""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console

from core.agent_harness.session.pending_choice import (
    AskUserQuestion,
    format_ask_user_answers,
)
from core.agent_harness.task_plan.plan import PlanStepStatus
from core.agent_harness.tools.tool_context import ActionToolScope
from surfaces.interactive_shell.session import Session
from tools.interactive_shell.actions.update_plan import execute_update_plan_tool


def _ctx(
    session: Session | None = None,
    *,
    turn_user_message: str = "",
) -> ActionToolScope:
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    return ActionToolScope(
        session=session if session is not None else Session(),
        console=console,
        turn_user_message=turn_user_message,
    )


def _ask_user_turn_text() -> str:
    return format_ask_user_answers(
        (
            AskUserQuestion(label="Onset", title="When did it start?", options=("Gradual",)),
            AskUserQuestion(label="Signal", title="Strongest signal?", options=("CPU",)),
        ),
        ("Gradual", "CPU"),
    )


_PLAN: list[dict[str, Any]] = [
    {"step": "Pinpoint onset on p99", "status": "pending"},
    {"step": "Confirm checkout returns 2xx", "status": "pending"},
]


def test_update_plan_strips_plan_only_after_ask_user_answers() -> None:
    session = Session()
    result = execute_update_plan_tool(
        {"plan": _PLAN, "plan_only": True},
        _ctx(session=session, turn_user_message=_ask_user_turn_text()),
    )

    assert result["ok"] is True
    assert session.plan_only_until_authorized is False
    assert session.task_plan is not None
    assert session.task_plan.steps[0].status is PlanStepStatus.IN_PROGRESS
    assert "Execution is authorized" in result["instruction"]


def test_update_plan_honors_plan_only_on_normal_turn() -> None:
    session = Session()
    result = execute_update_plan_tool(
        {"plan": _PLAN, "plan_only": True},
        _ctx(session=session, turn_user_message="plan only — do not run yet"),
    )

    assert result["ok"] is True
    assert session.plan_only_until_authorized is True
    assert session.task_plan is not None
    assert session.task_plan.all_pending is True
    assert "Plan-only" in result["instruction"]


def test_update_plan_honors_plan_only_after_ask_user_when_workload_flag_set() -> None:
    session = Session()
    session.plan_only_until_authorized = True
    result = execute_update_plan_tool(
        {"plan": _PLAN, "plan_only": True},
        _ctx(session=session, turn_user_message=_ask_user_turn_text()),
    )

    assert result["ok"] is True
    assert session.plan_only_until_authorized is True
    assert session.task_plan is not None
    assert session.task_plan.all_pending is True


def test_update_plan_model_false_cannot_clear_plan_only_after_ask_user() -> None:
    session = Session()
    session.plan_only_until_authorized = True
    result = execute_update_plan_tool(
        {"plan": _PLAN, "plan_only": False},
        _ctx(session=session, turn_user_message=_ask_user_turn_text()),
    )

    assert result["ok"] is True
    assert session.plan_only_until_authorized is True
    assert session.task_plan is not None
    assert session.task_plan.all_pending is True
    assert "Plan-only" in result["instruction"]

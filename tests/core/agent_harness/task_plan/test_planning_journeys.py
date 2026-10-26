"""Cross-seam journeys for the live task plan (update_plan + plan-only latch).

These pin the planning feature as a user-facing capability: the checklist is
the durable record, a failed or cancelled update does not commit, resume keeps
the confirmation boundary, and the prompt/gate honor that latch.
"""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console

from core.agent_harness.session import InMemorySessionStore, SessionCore, SessionManager
from core.agent_harness.session.pending_choice import AskUserQuestion, format_ask_user_answers
from core.agent_harness.task_plan.persist import (
    TASK_PLAN_STATE_CUSTOM_TYPE,
    apply_task_plan_state,
    should_persist_task_plan_state,
    task_plan_state_snapshot,
)
from core.agent_harness.task_plan.plan import PlanStepStatus, parse_task_plan
from core.agent_harness.task_plan.prompt import current_task_plan_block
from core.agent_harness.task_plan.update_plan_policy import apply_update_plan_session
from core.agent_harness.tools.tool_context import (
    ACTION_TOOL_CONTEXT_RESOURCE_KEY,
    ActionToolScope,
)
from core.tool.contracts import AgentToolContext
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.action_rendering import ActionRenderObserver
from surfaces.interactive_shell.ui.execution_confirm import execution_allowed
from tools.interactive_shell.action_names import ActionToolName
from tools.interactive_shell.actions.update_plan import (
    execute_update_plan_tool,
    run_update_plan,
    update_plan_tool,
)
from tools.interactive_shell.shared import allow_tool


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False)


def _scope(
    session: Session,
    *,
    turn_user_message: str = "",
    console: Any = None,
) -> ActionToolScope:
    return ActionToolScope(
        session=session,
        console=console if console is not None else _console(),
        turn_user_message=turn_user_message,
    )


def _agent_context(scope: ActionToolScope) -> AgentToolContext:
    return AgentToolContext(
        resolved_integrations={},
        resources={ACTION_TOOL_CONTEXT_RESOURCE_KEY: scope},
    )


def _pending_plan(*, explanation: str = "") -> list[dict[str, str]]:
    return [
        {"step": "Capture checkout 502 samples", "status": "pending"},
        {"step": "Confirm checkout returns 2xx", "status": "pending"},
    ]


def _ask_user_turn_text() -> str:
    return format_ask_user_answers(
        (
            AskUserQuestion(label="Onset", title="When did it start?", options=("Gradual",)),
            AskUserQuestion(label="Signal", title="Strongest signal?", options=("CPU",)),
        ),
        ("Gradual", "CPU"),
    )


class _CancelConsole:
    cancel_requested = True

    def print(self, *_args: object, **_kwargs: object) -> None:
        return


def test_plan_only_survives_flush_restore_and_still_gates_mutating_tools() -> None:
    storage = InMemorySessionStore()
    live = Session(store=storage)
    storage.open_session(live)
    storage.append_turn(live, "chat", "start")
    result = execute_update_plan_tool(
        {"plan": _pending_plan(), "plan_only": True, "explanation": "do not run yet"},
        _scope(live),
    )
    assert result["ok"] is True
    assert live.plan_only_until_authorized is True
    assert live.task_plan is not None
    assert live.task_plan.all_pending is True

    storage.flush(live)
    records = storage.read(live.session_id)
    content = next(
        rec["content"] for rec in records if rec.get("custom_type") == TASK_PLAN_STATE_CUSTOM_TYPE
    )

    restored = Session()
    SessionManager(store=InMemorySessionStore()).restore_context(
        restored,
        {
            "cli_agent_messages": [],
            "accumulated_context": {},
            "task_plan_state": content,
            "history": [],
        },
    )
    assert restored.task_plan is not None
    assert restored.task_plan.all_pending is True
    assert restored.plan_only_until_authorized is True
    assert "Execution is authorized" not in current_task_plan_block(
        restored.task_plan, plan_only=True
    )

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    assert not execution_allowed(
        allow_tool("shell"),
        session=restored,
        console=console,
        action_summary="!deploy",
        confirm_fn=lambda _: "n",
        is_tty=True,
    )
    assert restored.plan_only_until_authorized is True
    assert "Command to approve" in buf.getvalue()


def test_failed_update_plan_does_not_replace_or_persist_an_existing_plan() -> None:
    session = Session()
    first = execute_update_plan_tool({"plan": _pending_plan(), "plan_only": True}, _scope(session))
    assert first["ok"] is True
    before = task_plan_state_snapshot(session)
    assert before is not None
    assert before["plan_only_until_authorized"] is True

    failed = execute_update_plan_tool(
        {
            "plan": [
                {"step": "First", "status": "in_progress"},
                {"step": "Second", "status": "in_progress"},
            ],
            "plan_only": False,
        },
        _scope(session),
    )
    assert failed["ok"] is False
    assert session.task_plan is not None
    assert session.task_plan.all_pending is True
    assert session.plan_only_until_authorized is True
    after = task_plan_state_snapshot(session)
    assert after == before


def test_cancelled_update_plan_wrapper_does_not_commit_session_state() -> None:
    session = Session()
    scope = _scope(session, console=_CancelConsole())
    result = run_update_plan(
        plan=_pending_plan(),
        plan_only=True,
        context=_agent_context(scope),
    )
    assert result["ok"] is False
    assert result["cancelled"] is True
    assert session.task_plan is None
    assert session.plan_only_until_authorized is False
    assert task_plan_state_snapshot(session) is None


def test_ask_user_answers_without_latch_authorize_execution_and_prompt() -> None:
    session = Session()
    result = execute_update_plan_tool(
        {"plan": _pending_plan(), "plan_only": True, "explanation": "### Facts\n- gradual"},
        _scope(session, turn_user_message=_ask_user_turn_text()),
    )
    assert result["ok"] is True
    assert session.plan_only_until_authorized is False
    assert session.task_plan is not None
    assert session.task_plan.steps[0].status is PlanStepStatus.IN_PROGRESS
    assert "Execution is authorized" in result["instruction"]
    block = current_task_plan_block(session.task_plan, plan_only=False)
    assert "in progress" in block
    assert "Do not conclude this turn while a step is in_progress" in block


def test_ask_user_answers_with_latch_stay_plan_only_through_prompt_and_persist() -> None:
    session = Session()
    session.plan_only_until_authorized = True
    result = execute_update_plan_tool(
        {"plan": _pending_plan(), "plan_only": False},
        _scope(session, turn_user_message=_ask_user_turn_text()),
    )
    assert result["ok"] is True
    assert session.plan_only_until_authorized is True
    assert session.task_plan is not None
    assert session.task_plan.all_pending is True
    assert "Plan-only" in result["instruction"]
    block = current_task_plan_block(session.task_plan, plan_only=True)
    assert "Execution is authorized" not in block
    snapshot = task_plan_state_snapshot(session)
    assert snapshot is not None and snapshot["plan_only_until_authorized"] is True


def test_observer_does_not_commit_before_tool_success_so_flush_stays_empty() -> None:
    session = Session()
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=100)
    observer = ActionRenderObserver(session=session, console=console, message="plan the fix")
    observer(
        "tool_start",
        {
            "id": "p1",
            "name": ActionToolName.UPDATE_PLAN,
            "input": {"plan": _pending_plan(), "plan_only": True},
        },
    )
    assert session.task_plan is None
    assert task_plan_state_snapshot(session) is None
    observer(
        "tool_end",
        {"id": "p1", "name": ActionToolName.UPDATE_PLAN, "output": {"ok": False, "error": "nope"}},
    )
    assert session.task_plan is None
    assert not should_persist_task_plan_state(None, prior_records=[])


def test_successful_tool_then_observer_does_not_double_commit_or_dump_transcript() -> None:
    """Tool commits once; observer must not re-commit or dump the plan into scrollback.

    The checklist lives in the pinned bottom overlay from session state — a
    successful ``update_plan`` must not print ``Plan ready`` / diagnosis prose
    into the transcript (see ``test_update_plan_is_not_dumped_into_the_transcript``).
    """
    session = Session()
    result = execute_update_plan_tool(
        {"plan": _pending_plan(), "plan_only": True, "explanation": "### Facts\n- p99 up"},
        _scope(session),
    )
    assert result["ok"] is True
    first_snapshot = task_plan_state_snapshot(session)

    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=100)
    observer = ActionRenderObserver(session=session, console=console, message="plan the fix")
    observer("tool_end", {"id": "p1", "name": ActionToolName.UPDATE_PLAN, "output": {"ok": True}})
    assert "Plan ready" not in buffer.getvalue()
    assert "p99 up" not in buffer.getvalue()
    assert task_plan_state_snapshot(session) == first_snapshot


def test_identical_plan_snapshot_is_not_flushed_twice() -> None:
    storage = InMemorySessionStore()
    session = SessionCore(store=storage)
    storage.open_session(session)
    storage.append_turn(session, "chat", "start")
    plan, error = parse_task_plan({"plan": _pending_plan()})
    assert error is None and plan is not None
    apply_update_plan_session(session, plan, plan_only=True)
    storage.flush(session)
    first = [
        rec
        for rec in storage.read(session.session_id)
        if rec.get("custom_type") == TASK_PLAN_STATE_CUSTOM_TYPE
    ]
    storage.flush(session)
    second = [
        rec
        for rec in storage.read(session.session_id)
        if rec.get("custom_type") == TASK_PLAN_STATE_CUSTOM_TYPE
    ]
    assert len(second) == len(first)


def test_session_clear_drops_the_checklist_and_the_latch() -> None:
    session = SessionCore(store=InMemorySessionStore())
    plan, error = parse_task_plan({"plan": _pending_plan()})
    assert error is None and plan is not None
    apply_update_plan_session(session, plan, plan_only=True)
    session.clear()
    assert session.task_plan is None
    assert session.plan_only_until_authorized is False
    assert task_plan_state_snapshot(session) is None


def test_revision_keeps_the_latch_and_updates_the_durable_record() -> None:
    session = Session()
    execute_update_plan_tool({"plan": _pending_plan(), "plan_only": True}, _scope(session))
    revised: list[dict[str, Any]] = [
        {"step": "Inspect the failing Actions job", "status": "pending"},
        {"step": "Patch the workflow from the error", "status": "pending"},
        {"step": "Confirm the workflow run is green", "status": "pending"},
    ]
    result = execute_update_plan_tool(
        {"plan": revised, "plan_only": False, "explanation": "split the verify step"},
        _scope(session),
    )
    assert result["ok"] is True
    assert session.plan_only_until_authorized is True
    assert session.task_plan is not None
    assert session.task_plan.total == 3
    assert session.task_plan.explanation == "split the verify step"
    snapshot = task_plan_state_snapshot(session)
    restored = Session()
    apply_task_plan_state(restored, snapshot)
    assert restored.plan_only_until_authorized is True
    assert restored.task_plan is not None
    assert restored.task_plan.total == 3


def test_update_plan_tool_is_the_registered_action_name() -> None:
    assert update_plan_tool.name == ActionToolName.UPDATE_PLAN

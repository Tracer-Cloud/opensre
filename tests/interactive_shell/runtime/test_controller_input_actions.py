"""Pure input-action decisions for the interactive shell controller."""

from __future__ import annotations

import pytest

from surfaces.interactive_shell.runtime.input import (
    InputCancelled,
    InputClosed,
    InputEvent,
    InputSubmitted,
)
from surfaces.interactive_shell.runtime.input.actions import (
    QUEUE_DURING_CONFIRMATION_WARNING,
    CloseShell,
    DeliverConfirmation,
    IgnoreInput,
    InflightControl,
    RunInflightControl,
    ShellInputSnapshot,
    SubmitTurn,
    decide_input_action,
)


def _decide(
    event: InputEvent,
    *,
    exit_requested: bool = False,
    dispatch_running: bool = False,
    awaiting_confirmation: bool = False,
    worker_running: bool = False,
    needs_exclusive_stdin: bool = False,
) -> object:
    return decide_input_action(
        event,
        ShellInputSnapshot(
            exit_requested=exit_requested,
            dispatch_running=dispatch_running,
            awaiting_confirmation=awaiting_confirmation,
            worker_running=worker_running,
        ),
        needs_exclusive_stdin=lambda _text: needs_exclusive_stdin,
    )


def test_decide_closes_on_input_closed() -> None:
    assert _decide(InputClosed()) == CloseShell()


def test_decide_cancels_on_input_cancelled() -> None:
    assert _decide(InputCancelled()) == RunInflightControl(control=InflightControl.CANCEL_TURN)


@pytest.mark.parametrize("text", ["", "   "])
def test_decide_ignores_empty_or_blank_submissions(text: str) -> None:
    assert _decide(InputSubmitted(text)) == IgnoreInput()


def test_decide_ignores_submitted_input_after_exit_requested() -> None:
    assert _decide(InputSubmitted("/status"), exit_requested=True) == IgnoreInput()


def test_decide_cancels_when_cancel_request_is_typed_during_dispatch() -> None:
    assert _decide(InputSubmitted(" /cancel "), dispatch_running=True) == RunInflightControl(
        control=InflightControl.CANCEL_TURN,
        submitted_text="/cancel",
    )


def test_decide_routes_goal_pause_as_an_inflight_control() -> None:
    action = _decide(
        InputSubmitted(" /goal pause "),
        dispatch_running=True,
        needs_exclusive_stdin=True,
    )

    assert action == RunInflightControl(
        control=InflightControl.PAUSE_GOAL,
        submitted_text="/goal pause",
    )


@pytest.mark.parametrize(
    ("text", "control"),
    [
        ("/goal pause", InflightControl.PAUSE_GOAL),
        ("/goal clear", InflightControl.CLEAR_GOAL),
        ("/goal unset", InflightControl.CLEAR_GOAL),
        ("/exit", InflightControl.EXIT_SHELL),
    ],
)
def test_decide_routes_controls_around_a_live_daemon_worker(
    text: str,
    control: InflightControl,
) -> None:
    assert _decide(InputSubmitted(text), worker_running=True) == RunInflightControl(
        control=control,
        submitted_text=text,
    )


@pytest.mark.parametrize("text", ["/goal clear", " /goal unset "])
def test_decide_routes_goal_clear_as_an_inflight_control(text: str) -> None:
    action = _decide(
        InputSubmitted(text),
        dispatch_running=True,
        needs_exclusive_stdin=True,
    )

    assert isinstance(action, RunInflightControl)
    assert action.control.value == "clear_goal"


@pytest.mark.parametrize("text", ["/exit", " /quit "])
def test_decide_routes_exit_as_an_inflight_control(text: str) -> None:
    action = _decide(
        InputSubmitted(text),
        dispatch_running=True,
        needs_exclusive_stdin=True,
    )

    assert isinstance(action, RunInflightControl)
    assert action.control.value == "exit_shell"


@pytest.mark.parametrize("text", ["/goal clear now", "/exit now", "please /quit"])
def test_inflight_controls_require_an_exact_literal(text: str) -> None:
    assert _decide(InputSubmitted(text), dispatch_running=True) == SubmitTurn(text=text)


def test_decide_delivers_stripped_confirmation_answer() -> None:
    assert _decide(
        InputSubmitted(" yes "),
        awaiting_confirmation=True,
    ) == DeliverConfirmation(text="yes")


def test_decide_submits_non_confirmation_input_while_confirmation_is_pending() -> None:
    assert _decide(
        InputSubmitted("run /status"),
        awaiting_confirmation=True,
    ) == SubmitTurn(
        text="run /status",
        warning=QUEUE_DURING_CONFIRMATION_WARNING,
    )


def test_decide_submits_normal_turn_without_wait_by_default() -> None:
    assert _decide(InputSubmitted("  show status  ")) == SubmitTurn(text="show status")


def test_decide_strips_pasted_shell_prompt_chrome() -> None:
    assert _decide(
        InputSubmitted("[1] ❯ [1] ❯ what windows users number did open opensre during last 7 days?")
    ) == SubmitTurn(
        text="what windows users number did open opensre during last 7 days?",
    )


def test_decide_submits_text_matching_the_placeholder() -> None:
    assert _decide(InputSubmitted("see what you can do")) == SubmitTurn(text="see what you can do")


def test_decide_submits_normal_turn_with_exclusive_stdin_wait() -> None:
    assert _decide(InputSubmitted("/integrations"), needs_exclusive_stdin=True) == SubmitTurn(
        text="/integrations",
        wait_until_idle=True,
    )


def test_only_exclusive_stdin_commands_hold_the_next_prompt() -> None:
    """A ``/goal`` work turn keeps the prompt open so the spinner and status row paint."""
    from surfaces.interactive_shell.runtime.input.actions import SubmitTurn

    assert SubmitTurn(text="count the open PRs", wait_until_idle=False).wait_until_idle is False
    assert SubmitTurn(text="/onboard", wait_until_idle=True).wait_until_idle is True


def _plan(*statuses: str):
    from core.agent_harness.task_plan.plan import parse_task_plan

    items = [{"step": f"Step {i}", "status": status} for i, status in enumerate(statuses, start=1)]
    plan, error = parse_task_plan({"plan": items})
    assert error is None and plan is not None
    return plan


def _controller():
    from io import StringIO

    from rich.console import Console

    from surfaces.interactive_shell.controller import InteractiveShellController
    from surfaces.interactive_shell.session import Session

    captured = Console(file=StringIO(), force_terminal=False, width=80)
    return InteractiveShellController(Session(), console=captured)


@pytest.mark.asyncio
async def test_goal_control_after_cancelled_waiter_waits_for_detached_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A control submitted after Esc remains durable instead of joining the turn queue."""
    from contextlib import nullcontext

    from core.agent_harness.session import InMemorySessionStore, SessionManager
    from core.agent_harness.session_goal.goal import SessionGoal, attach_session_goal
    from core.agent_harness.session_goal.persist import pending_session_goal_controls
    from core.agent_harness.spi.cancel import HostCancelReason

    controller = _controller()
    store = InMemorySessionStore()
    controller.session.store = store
    store.open_session(controller.session)
    store.append_turn(controller.session, "chat", "seed")
    attach_session_goal(controller.session, SessionGoal(condition="finish safely"))
    controller.session.task_plan = _plan("in_progress", "pending")
    SessionManager.for_session(controller.session).flush(controller.session)
    release_callbacks: list[object] = []

    monkeypatch.setattr(
        type(controller.turn_runtime),
        "has_live_turn_worker",
        lambda _runtime: True,
    )
    monkeypatch.setattr(
        type(controller.turn_runtime),
        "run_after_turn_worker",
        lambda _runtime, callback: release_callbacks.append(callback),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.controller.session_execution_lock",
        lambda _session_id: nullcontext(),
    )

    kept = await controller._handle_input_action(
        RunInflightControl(
            control=InflightControl.CLEAR_GOAL,
            submitted_text="/goal clear",
        )
    )

    assert kept is True
    assert controller.state.queue.empty()
    assert [
        control["reason"]
        for control in pending_session_goal_controls(store.read(controller.session.session_id))
    ] == [HostCancelReason.GOAL_CLEAR.value]
    assert len(release_callbacks) == 1
    callback = release_callbacks.pop()
    assert callable(callback)
    callback()

    assert controller.session.session_goal is None
    assert controller.session.task_plan is None
    assert pending_session_goal_controls(store.read(controller.session.session_id)) == []


@pytest.mark.asyncio
async def test_detached_goal_control_write_failure_keeps_the_shell_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller()
    release_callbacks: list[object] = []

    monkeypatch.setattr(
        type(controller.turn_runtime),
        "has_live_turn_worker",
        lambda _runtime: True,
    )
    monkeypatch.setattr(
        type(controller.turn_runtime),
        "run_after_turn_worker",
        lambda _runtime, callback: release_callbacks.append(callback),
    )

    def _fail_to_persist(_reason: object) -> str:
        raise OSError("disk full")

    monkeypatch.setattr(controller, "_persist_goal_control_for_resume", _fail_to_persist)

    kept = await controller._handle_input_action(
        RunInflightControl(
            control=InflightControl.PAUSE_GOAL,
            submitted_text="/goal pause",
        )
    )

    assert kept is True
    assert not controller.state.exit_requested
    assert len(release_callbacks) == 1


@pytest.mark.asyncio
async def test_detached_goal_control_write_failure_survives_an_immediate_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.agent_harness.spi.cancel import HostCancelReason

    controller = _controller()
    release_callbacks: list[object] = []
    deferred_closes: list[tuple[object, object, object]] = []

    def _capture_after_worker(_runtime: object, callback: object) -> None:
        release_callbacks.append(callback)

    def _fail_to_persist(_reason: object) -> str:
        raise OSError("disk full")

    def _capture_deferred_close(
        session: object,
        fallback_goal_control: object,
        exit_command: object,
    ) -> None:
        deferred_closes.append((session, fallback_goal_control, exit_command))

    monkeypatch.setattr(
        type(controller.turn_runtime), "has_live_turn_worker", lambda _runtime: True
    )
    monkeypatch.setattr(
        type(controller.turn_runtime), "run_after_turn_worker", _capture_after_worker
    )
    monkeypatch.setattr(controller, "_persist_goal_control_for_resume", _fail_to_persist)
    monkeypatch.setattr(
        "surfaces.interactive_shell.controller.close_repl_session_after_detached_worker",
        _capture_deferred_close,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.controller.finish_shell_exit",
        lambda _session, _console: None,
    )

    assert await controller._handle_input_action(
        RunInflightControl(
            control=InflightControl.CLEAR_GOAL,
            submitted_text="/goal clear",
        )
    )
    assert not await controller._handle_input_action(
        RunInflightControl(
            control=InflightControl.EXIT_SHELL,
            submitted_text="/exit",
        )
    )

    await controller._shutdown_runtime()

    assert len(release_callbacks) == 2
    callback = release_callbacks[-1]
    assert callable(callback)
    callback()
    assert deferred_closes == [(controller.session, HostCancelReason.GOAL_CLEAR, "/exit")]


def test_goal_control_boundary_write_failure_does_not_stop_the_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import nullcontext

    from core.agent_harness.session_goal.goal import SessionGoal, attach_session_goal
    from core.agent_harness.spi.cancel import HostCancelReason

    controller = _controller()
    attach_session_goal(controller.session, SessionGoal(condition="finish safely"))

    def _fail_to_flush(_session: object) -> None:
        raise OSError("disk full")

    def _unlocked(*_args: object, **_kwargs: object) -> object:
        return nullcontext()

    monkeypatch.setattr(
        "surfaces.interactive_shell.controller.session_execution_lock",
        _unlocked,
    )
    monkeypatch.setattr(
        controller.session.store,
        "flush_session_goal_control_state",
        _fail_to_flush,
    )

    assert controller._try_apply_goal_control_after_worker_release(HostCancelReason.GOAL_CLEAR)


@pytest.mark.asyncio
async def test_idle_continuation_keeps_an_unfinished_plan() -> None:
    """A plan waiting between turns must survive the next typed prompt.

    Dispatch is idle then, so clearing on ``is_dispatch_running() is False``
    would drop pending-step state and the pinned overlay before the turn runs.
    """
    controller = _controller()
    plan = _plan("in_progress", "pending")
    controller.session.task_plan = plan

    kept = await controller._handle_input_action(SubmitTurn(text="continue the plan"))

    assert kept is True
    assert controller.session.task_plan is plan


@pytest.mark.asyncio
async def test_idle_go_keeps_an_all_pending_plan() -> None:
    """Plan-only overlay invites ``go``; that submit must not wipe the checklist."""
    controller = _controller()
    plan = _plan("pending", "pending")
    controller.session.task_plan = plan
    controller.session.plan_only_until_authorized = True

    kept = await controller._handle_input_action(SubmitTurn(text="go"))

    assert kept is True
    assert controller.session.task_plan is plan


@pytest.mark.asyncio
async def test_idle_new_turn_clears_a_completed_plan() -> None:
    """A finished plan must not linger over the next unrelated typed turn."""
    controller = _controller()
    controller.session.task_plan = _plan("completed", "completed")
    controller.session.task_plan_work = [["Prior work"], []]
    controller.session.task_plan_work_step_texts = ("Step 1", "Step 2")
    controller.session.plan_only_until_authorized = True

    kept = await controller._handle_input_action(SubmitTurn(text="new question"))

    assert kept is True
    assert controller.session.task_plan is None
    assert controller.session.task_plan_work == []
    assert controller.session.task_plan_work_step_texts is None
    assert controller.session.plan_only_until_authorized is False


@pytest.mark.asyncio
async def test_cancelling_a_running_turn_keeps_its_skill_and_plan() -> None:
    import asyncio
    import threading

    controller = _controller()
    plan = _plan("in_progress", "pending")
    controller.session.task_plan = plan
    controller.session.active_skill = "scheduling-github-ci-repairs"
    cancel_event = threading.Event()

    async def _hold() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(_hold())
    controller.state.start_dispatch(task=task, cancel_event=cancel_event)
    try:
        kept = await controller._handle_input_action(
            RunInflightControl(control=InflightControl.CANCEL_TURN)
        )

        assert kept is True
        assert cancel_event.is_set()
        assert controller.session.task_plan is plan
        assert controller.session.active_skill == "scheduling-github-ci-repairs"
    finally:
        task.cancel()
        _ = await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_exit_control_stops_the_running_dispatch_without_queueing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import threading

    from core.agent_harness.spi.cancel import HostCancelEvent

    worker_started = threading.Event()
    release_turn = threading.Event()
    turn_finished = threading.Event()
    flushed_after_turn: list[bool] = []
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.exit_control._flush_analytics_on_exit",
        lambda _console: flushed_after_turn.append(turn_finished.is_set()),
    )
    controller = _controller()
    cancel = HostCancelEvent()

    def _hold() -> None:
        worker_started.set()
        cancel.wait()
        release_turn.wait()
        turn_finished.set()

    task = asyncio.create_task(asyncio.to_thread(_hold))
    controller.state.start_dispatch(task=task, cancel_event=cancel)
    try:
        assert await asyncio.to_thread(worker_started.wait, 1)
        kept = await controller._handle_input_action(
            RunInflightControl(
                control=InflightControl.EXIT_SHELL,
                submitted_text="/exit",
            )
        )

        assert kept is False
        assert controller.state.exit_requested
        assert cancel.is_set()
        assert not task.cancelled()
        assert controller.state.queue.empty()
        assert flushed_after_turn == []
        assert controller.session.history == []

        shutdown = asyncio.create_task(controller._shutdown_runtime())
        await asyncio.sleep(0)
        assert not shutdown.done()
        assert flushed_after_turn == []
        release_turn.set()
        await shutdown

        assert turn_finished.is_set()
        assert flushed_after_turn == [True]
        assert controller.session.history[-1]["type"] == "slash"
        assert controller.session.history[-1]["text"] == "/exit"
    finally:
        release_turn.set()
        task.cancel()
        _ = await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_exit_control_defers_close_while_only_daemon_worker_remains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deferred_closes: list[object] = []
    exit_finished: list[bool] = []
    controller = _controller()

    def _capture_deferred_close(_runtime: object, callback: object) -> None:
        deferred_closes.append(callback)

    monkeypatch.setattr(
        type(controller.turn_runtime),
        "has_live_turn_worker",
        lambda _runtime: True,
    )
    monkeypatch.setattr(
        type(controller.turn_runtime),
        "run_after_turn_worker",
        _capture_deferred_close,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.controller.finish_shell_exit",
        lambda _session, _console: exit_finished.append(True),
    )

    kept = await controller._handle_input_action(
        RunInflightControl(
            control=InflightControl.EXIT_SHELL,
            submitted_text="/exit",
        )
    )
    await controller._shutdown_runtime()

    assert kept is False
    assert controller.state.has_detached_turn_worker()
    assert controller.session.history == []
    assert exit_finished == [True]
    assert len(deferred_closes) == 1


@pytest.mark.asyncio
async def test_closed_input_bounds_uncooperative_worker_and_defers_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import threading

    from core.agent_harness.spi.cancel import HostCancelEvent

    worker_started = threading.Event()
    release_turn = threading.Event()
    deferred_closes: list[object] = []
    monkeypatch.setattr(
        "surfaces.interactive_shell.controller._INFLIGHT_EXIT_DRAIN_TIMEOUT_SECONDS",
        0.01,
    )
    controller = _controller()

    def _capture_deferred_close(_runtime: object, callback: object) -> None:
        deferred_closes.append(callback)

    monkeypatch.setattr(
        type(controller.turn_runtime),
        "run_after_turn_worker",
        _capture_deferred_close,
    )
    cancel = HostCancelEvent()

    def _hold() -> None:
        worker_started.set()
        release_turn.wait()

    task = asyncio.create_task(asyncio.to_thread(_hold))
    controller.state.start_dispatch(task=task, cancel_event=cancel)
    try:
        assert await asyncio.to_thread(worker_started.wait, 1)
        assert await controller._handle_input_action(CloseShell()) is False

        await asyncio.wait_for(controller._shutdown_runtime(), timeout=0.5)

        assert cancel.is_set()
        assert task.cancelled()
        assert controller.state.has_detached_turn_worker()
        assert len(deferred_closes) == 1
    finally:
        release_turn.set()
        task.cancel()
        _ = await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_exit_control_bounds_uncooperative_worker_and_persists_goal_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import threading

    from core.agent_harness.session import InMemorySessionStore, SessionManager
    from core.agent_harness.session.persistence.contracts import RestoreContextKey
    from core.agent_harness.session_goal.goal import SessionGoal, attach_session_goal
    from core.agent_harness.session_goal.persist import (
        SESSION_GOAL_STATE_CUSTOM_TYPE,
        pending_session_goal_controls,
    )
    from core.agent_harness.spi.cancel import HostCancelEvent, HostCancelReason
    from surfaces.interactive_shell.session import Session

    worker_started = threading.Event()
    release_turn = threading.Event()
    worker_finished = threading.Event()
    exit_finished: list[bool] = []
    deferred_closes: list[object] = []
    deferred_finalizations: list[tuple[object, object, object]] = []
    monkeypatch.setattr(
        "surfaces.interactive_shell.controller._INFLIGHT_EXIT_DRAIN_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.controller.finish_shell_exit",
        lambda _session, _console: exit_finished.append(True),
    )
    controller = _controller()

    def _capture_deferred_close(_runtime: object, callback: object) -> None:
        deferred_closes.append(callback)

    def _finalize_after_detach(
        session: object,
        fallback_goal_control: object,
        exit_command: object,
    ) -> None:
        deferred_finalizations.append((session, fallback_goal_control, exit_command))

    monkeypatch.setattr(
        type(controller.turn_runtime),
        "run_after_turn_worker",
        _capture_deferred_close,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.controller.close_repl_session_after_detached_worker",
        _finalize_after_detach,
    )
    store = InMemorySessionStore()
    controller.session.store = store
    store.open_session(controller.session)
    store.append_turn(controller.session, "chat", "seed")
    attach_session_goal(
        controller.session,
        SessionGoal(condition="keep going", max_outer_turns=4),
    )
    SessionManager.for_session(controller.session).flush(controller.session)
    cancel = HostCancelEvent()
    cancel.request(HostCancelReason.GOAL_CLEAR)

    def _hold() -> None:
        worker_started.set()
        release_turn.wait()
        worker_finished.set()

    task = asyncio.create_task(asyncio.to_thread(_hold))
    controller.state.start_dispatch(task=task, cancel_event=cancel)
    try:
        assert await asyncio.to_thread(worker_started.wait, 1)
        kept = await controller._handle_input_action(
            RunInflightControl(
                control=InflightControl.EXIT_SHELL,
                submitted_text="/exit",
            )
        )

        assert kept is False
        await asyncio.wait_for(controller._shutdown_runtime(), timeout=0.5)

        assert task.cancelled()
        assert controller.state.has_detached_turn_worker()
        assert exit_finished == [True]
        assert len(deferred_closes) == 1
        assert deferred_finalizations == []
        stored_records = store.read(controller.session.session_id)
        goal_records = [
            record
            for record in stored_records
            if record.get("type") == "custom_message"
            and record.get("custom_type") == SESSION_GOAL_STATE_CUSTOM_TYPE
        ]
        pending_controls = pending_session_goal_controls(stored_records)
        assert controller.session.session_goal is not None
        assert goal_records[-1].get("content", {}).get("session_goal", {}).get("status") == "active"
        assert [control["reason"] for control in pending_controls] == ["goal_clear"]
        assert sum(record.get("type") == "leaf" for record in stored_records) == 1

        restored = Session(session_id=controller.session.session_id, store=store)
        SessionManager(store=store).restore_context(
            restored,
            {
                RestoreContextKey.SESSION_GOAL_STATE: goal_records[-1]["content"],
                RestoreContextKey.SESSION_GOAL_CONTROLS: pending_controls,
            },
        )

        resumed_records = store.read(restored.session_id)
        resumed_goal_records = [
            record
            for record in resumed_records
            if record.get("custom_type") == SESSION_GOAL_STATE_CUSTOM_TYPE
        ]
        assert restored.session_goal is None
        assert resumed_goal_records[-1].get("content", {}).get("session_goal") is None
        assert pending_session_goal_controls(resumed_records) == []

        release_turn.set()
        assert await asyncio.to_thread(worker_finished.wait, 1)
        deferred_close = deferred_closes.pop()
        assert callable(deferred_close)
        deferred_close()
        assert len(deferred_finalizations) == 1
        finalized_session, fallback_control, exit_command = deferred_finalizations[0]
        assert finalized_session is controller.session
        assert fallback_control is None
        assert exit_command == "/exit"
    finally:
        release_turn.set()
        task.cancel()
        _ = await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("reason_name", ["GOAL_PAUSE", "GOAL_CLEAR"])
def test_requesting_goal_control_soft_cancels_the_running_turn(reason_name: str) -> None:
    import asyncio

    from core.agent_harness.spi.cancel import HostCancelEvent, HostCancelReason
    from surfaces.interactive_shell.runtime.core.state import ReplState

    async def _scenario() -> None:
        state = ReplState()
        cancel_event = HostCancelEvent()

        async def _hold() -> None:
            await asyncio.Event().wait()

        task = asyncio.create_task(_hold())
        state.start_dispatch(task=task, cancel_event=cancel_event)
        try:
            reason = HostCancelReason[reason_name]
            state.request_goal_control(reason)

            assert state.requested_goal_control() is reason
            assert cancel_event.is_set()
            assert not task.cancelled()
        finally:
            task.cancel()
            _ = await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_scenario())


@pytest.mark.parametrize(
    ("control", "command"),
    [
        (InflightControl.PAUSE_GOAL, "/goal pause"),
        (InflightControl.CLEAR_GOAL, "/goal clear"),
    ],
)
@pytest.mark.asyncio
async def test_goal_control_runs_normally_if_dispatch_finishes_before_handling(
    control: InflightControl,
    command: str,
) -> None:
    from core.agent_harness.session_goal.goal import (
        SessionGoal,
        attach_session_goal,
        session_goal_is_paused,
    )
    from surfaces.interactive_shell.command_registry.dispatch import dispatch_slash

    controller = _controller()
    attach_session_goal(controller.session, SessionGoal(condition="keep going"))

    kept = await controller._handle_input_action(
        RunInflightControl(control=control, submitted_text=command)
    )

    assert kept is True
    assert controller.session.terminal.pending_inflight_goal_controls == {}
    queued = await controller.state.queue.get()
    controller.state.queue.task_done()
    assert queued == command
    assert dispatch_slash(queued, controller.session, controller.service_console)
    if control is InflightControl.PAUSE_GOAL:
        assert session_goal_is_paused(controller.session)
    else:
        assert controller.session.session_goal is None


@pytest.mark.asyncio
async def test_lower_priority_goal_control_is_consumed_with_the_active_dispatch() -> None:
    import asyncio

    from core.agent_harness.session_goal.goal import SessionGoal, attach_session_goal
    from core.agent_harness.spi.cancel import HostCancelEvent, HostCancelReason
    from core.agent_harness.spi.session_goal import apply_session_goal_control
    from surfaces.interactive_shell.command_registry.dispatch import dispatch_slash

    controller = _controller()
    attach_session_goal(controller.session, SessionGoal(condition="keep going"))

    async def _hold() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(_hold())
    cancel = HostCancelEvent()
    controller.state.start_dispatch(task=task, cancel_event=cancel)
    try:
        assert await controller._handle_input_action(
            RunInflightControl(
                control=InflightControl.CLEAR_GOAL,
                submitted_text="/goal clear",
            )
        )
        assert await controller._handle_input_action(
            RunInflightControl(
                control=InflightControl.PAUSE_GOAL,
                submitted_text="/goal pause",
            )
        )

        assert cancel.reason is HostCancelReason.GOAL_CLEAR
        assert controller.session.terminal.pending_inflight_goal_controls == {
            HostCancelReason.GOAL_CLEAR.value: 1,
            HostCancelReason.GOAL_PAUSE.value: 1,
        }
        assert apply_session_goal_control(controller.session, cancel.reason)
        replacement = attach_session_goal(
            controller.session,
            SessionGoal(condition="review the result"),
        )
        for _ in range(2):
            queued = await controller.state.queue.get()
            controller.state.queue.task_done()
            assert dispatch_slash(queued, controller.session, controller.service_console)

        assert controller.session.session_goal is replacement
    finally:
        task.cancel()
        _ = await asyncio.gather(task, return_exceptions=True)


def test_inflight_goal_pause_keeps_input_open_and_does_not_leak_to_queued_turns() -> None:
    import asyncio

    from core.agent_harness.session_goal.goal import (
        SessionGoal,
        SessionGoalStatus,
        attach_session_goal,
    )
    from core.agent_harness.spi.cancel import HostCancelEvent, HostCancelReason
    from surfaces.interactive_shell.runtime.turn_host import run_agent_turn_queue

    async def _scenario() -> None:
        from unittest.mock import AsyncMock

        controller = _controller()
        controller.prompt.suspend = AsyncMock()
        attach_session_goal(
            controller.session,
            SessionGoal(condition="keep going", max_outer_turns=4),
        )
        controller.session.terminal.pending_prompt_default = "keep going"
        controller.session.terminal.pending_prompt_autosubmit = True
        controller.session.terminal.pending_prompt_plain_turn = True
        started = asyncio.Event()
        finish_current_turn = asyncio.Event()
        submitted: list[str] = []
        cancel_events: list[HostCancelEvent] = []
        pause_seen: list[bool] = []
        goal_seen: list[tuple[str, frozenset[int]]] = []

        async def _run_turn(text: str) -> None:
            submitted.append(text)
            if text == "earlier queued turn":
                pause_seen.append(
                    controller.state.requested_goal_control() is HostCancelReason.GOAL_PAUSE
                )
                goal = controller.session.session_goal
                assert goal is not None
                goal_seen.append((goal.status, goal.completed))
                return
            if text != "current goal turn":
                return
            cancel_event = controller.state.current_cancel_event
            assert isinstance(cancel_event, HostCancelEvent)
            cancel_events.append(cancel_event)
            started.set()
            while not cancel_event.is_set():
                await asyncio.sleep(0)
            await finish_current_turn.wait()
            goal = controller.session.session_goal
            assert goal is not None
            assert goal.status == SessionGoalStatus.ACTIVE
            attach_session_goal(controller.session, goal.with_completed(frozenset({0})))
            controller.state.finish_dispatch(cancel_event)

        worker = asyncio.create_task(
            run_agent_turn_queue(
                state=controller.state,
                run_turn=_run_turn,
                on_goal_control=controller._apply_goal_control_at_turn_boundary,
            )
        )
        try:
            await controller.state.queue.put("current goal turn")
            await started.wait()
            await controller.state.queue.put("earlier queued turn")

            kept = await controller._handle_input_action(
                RunInflightControl(
                    control=InflightControl.PAUSE_GOAL,
                    submitted_text="/goal pause",
                )
            )

            assert kept is True
            assert controller.session.terminal.pending_inflight_goal_controls == {
                HostCancelReason.GOAL_PAUSE.value: 1
            }
            assert cancel_events[0].is_set()
            assert controller.session.session_goal is not None
            assert controller.session.session_goal.status == SessionGoalStatus.ACTIVE
            controller.prompt.suspend.assert_not_awaited()

            finish_current_turn.set()
            await asyncio.wait_for(controller.state.queue.join(), timeout=1)
            assert controller.session.terminal.pending_prompt_default is None
            assert controller.session.terminal.pending_prompt_autosubmit is False
            assert controller.session.terminal.pending_prompt_plain_turn is False
            assert submitted == ["current goal turn", "earlier queued turn", "/goal pause"]
            assert pause_seen == [False]
            assert goal_seen == [(SessionGoalStatus.PAUSED, frozenset({0}))]
        finally:
            controller.state.request_exit()
            await controller.state.queue.put("")
            await worker

    asyncio.run(_scenario())


@pytest.mark.asyncio
async def test_goal_pause_after_dispatch_finish_still_pauses_before_queued_work() -> None:
    import asyncio

    from core.agent_harness.session_goal.goal import (
        SessionGoal,
        SessionGoalStatus,
        attach_session_goal,
    )
    from core.agent_harness.spi.cancel import HostCancelEvent, HostCancelReason

    controller = _controller()
    attach_session_goal(
        controller.session,
        SessionGoal(condition="keep going", max_outer_turns=4),
    )

    from surfaces.interactive_shell.runtime.turn_host import run_agent_turn_queue

    dispatch_finished = asyncio.Event()
    finish_turn = asyncio.Event()
    submitted: list[str] = []
    statuses_before_queued_turns: list[SessionGoalStatus] = []

    async def _run_turn(text: str) -> None:
        submitted.append(text)
        if text == "current goal turn":
            first_cancel = controller.state.current_cancel_event
            assert isinstance(first_cancel, HostCancelEvent)
            controller.state.finish_dispatch(first_cancel)
            dispatch_finished.set()
            await finish_turn.wait()
            return
        goal = controller.session.session_goal
        assert goal is not None
        statuses_before_queued_turns.append(goal.status)

    worker = asyncio.create_task(
        run_agent_turn_queue(
            state=controller.state,
            run_turn=_run_turn,
            on_goal_control=controller._apply_goal_control_at_turn_boundary,
        )
    )

    try:
        await controller.state.queue.put("current goal turn")
        await dispatch_finished.wait()
        await controller.state.queue.put("earlier queued turn")
        assert controller.state.is_dispatch_running()
        assert controller.state.current_cancel_event is None

        kept = await controller._handle_input_action(
            RunInflightControl(
                control=InflightControl.PAUSE_GOAL,
                submitted_text="/goal pause",
            )
        )

        assert kept is True
        assert controller.session.session_goal is not None
        assert controller.session.session_goal.status == SessionGoalStatus.ACTIVE
        late_cancel = controller.state.current_cancel_event
        assert isinstance(late_cancel, HostCancelEvent)
        assert late_cancel.reason is HostCancelReason.GOAL_PAUSE
        assert late_cancel.is_set()
        finish_turn.set()
        await asyncio.wait_for(controller.state.queue.join(), timeout=1)
        assert controller.session.session_goal.status == SessionGoalStatus.PAUSED
        assert submitted == ["current goal turn", "earlier queued turn", "/goal pause"]
        assert statuses_before_queued_turns == [
            SessionGoalStatus.PAUSED,
            SessionGoalStatus.PAUSED,
        ]
    finally:
        controller.state.request_exit()
        await controller.state.queue.put("")
        await worker


@pytest.mark.asyncio
async def test_goal_clear_does_not_remove_a_goal_created_by_earlier_queued_work() -> None:
    import asyncio

    from core.agent_harness.session_goal.goal import SessionGoal, attach_session_goal
    from core.agent_harness.spi.cancel import HostCancelEvent, HostCancelReason
    from surfaces.interactive_shell.runtime.turn_host import run_agent_turn_queue

    controller = _controller()
    attach_session_goal(
        controller.session,
        SessionGoal(condition="keep going", max_outer_turns=4),
    )
    started = asyncio.Event()
    finish_current_turn = asyncio.Event()
    submitted: list[str] = []
    goals_before_queued_turns: list[object] = []
    replacement: SessionGoal | None = None

    async def _run_turn(text: str) -> None:
        nonlocal replacement
        submitted.append(text)
        if text == "current goal turn":
            started.set()
            await finish_current_turn.wait()
            return
        goals_before_queued_turns.append(controller.session.session_goal)
        if text == "earlier queued turn":
            replacement = attach_session_goal(
                controller.session,
                SessionGoal(condition="review the result", max_outer_turns=2),
            )
            return
        from surfaces.interactive_shell.command_registry.dispatch import dispatch_slash

        assert dispatch_slash(text, controller.session, controller.service_console)

    worker = asyncio.create_task(
        run_agent_turn_queue(
            state=controller.state,
            run_turn=_run_turn,
            on_goal_control=controller._apply_goal_control_at_turn_boundary,
        )
    )
    try:
        await controller.state.queue.put("current goal turn")
        await started.wait()
        await controller.state.queue.put("earlier queued turn")

        kept = await controller._handle_input_action(
            RunInflightControl(
                control=InflightControl.CLEAR_GOAL,
                submitted_text="/goal clear",
            )
        )

        assert kept is True
        cancel = controller.state.current_cancel_event
        assert isinstance(cancel, HostCancelEvent)
        assert cancel.reason is HostCancelReason.GOAL_CLEAR
        assert cancel.is_set()

        finish_current_turn.set()
        await asyncio.wait_for(controller.state.queue.join(), timeout=1)

        assert controller.session.session_goal is replacement
        assert submitted == ["current goal turn", "earlier queued turn", "/goal clear"]
        assert goals_before_queued_turns == [None, replacement]
    finally:
        controller.state.request_exit()
        await controller.state.queue.put("")
        await worker


@pytest.mark.asyncio
async def test_cancelled_goal_pause_boundary_does_not_wait_for_worker_lease() -> None:
    import asyncio
    import threading

    from core.agent_harness.session import InMemorySessionStore, SessionManager
    from core.agent_harness.session_goal.goal import (
        SessionGoal,
        SessionGoalStatus,
        attach_session_goal,
    )
    from core.agent_harness.session_goal.persist import SESSION_GOAL_STATE_CUSTOM_TYPE
    from core.agent_harness.spi.cancel import HostCancelReason
    from infrastructure.turn_host.session_lock import session_execution_lock
    from surfaces.interactive_shell.controller import InteractiveShellController
    from surfaces.interactive_shell.session import Session

    store = InMemorySessionStore()
    session = Session(store=store)
    store.open_session(session)
    store.append_turn(session, "chat", "seed")
    attach_session_goal(
        session,
        SessionGoal(condition="keep going", max_outer_turns=4),
    )
    SessionManager.for_session(session).flush(session)
    controller = InteractiveShellController(session)
    lease_acquired = threading.Event()
    release_lease = threading.Event()

    def _hold_worker_lease() -> None:
        with session_execution_lock(session.session_id):
            lease_acquired.set()
            release_lease.wait()

    worker = threading.Thread(target=_hold_worker_lease)
    worker.start()
    await asyncio.to_thread(lease_acquired.wait)
    try:
        pause_task = asyncio.create_task(
            controller._apply_goal_control_at_turn_boundary(HostCancelReason.GOAL_PAUSE)
        )
        await asyncio.sleep(0)

        assert session.session_goal is not None
        assert session.session_goal.status == SessionGoalStatus.ACTIVE
        pause_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pause_task, timeout=0.5)

        assert session.session_goal.status == SessionGoalStatus.ACTIVE
        release_lease.set()
        await asyncio.to_thread(worker.join, 1)
        assert not worker.is_alive()
        await controller._apply_goal_control_at_turn_boundary(HostCancelReason.GOAL_PAUSE)

        assert session.session_goal.status == SessionGoalStatus.PAUSED
        records = [
            record
            for record in store.read(session.session_id)
            if record.get("type") == "custom_message"
            and record.get("custom_type") == SESSION_GOAL_STATE_CUSTOM_TYPE
        ]
        assert records[-1].get("content", {}).get("session_goal", {}).get("status") == "paused"
    finally:
        release_lease.set()
        worker.join(timeout=1)
        assert not worker.is_alive()


@pytest.mark.asyncio
async def test_shutdown_persists_pause_after_boundary_wait_is_cancelled() -> None:
    import asyncio
    import contextlib
    import threading

    from core.agent_harness.session import InMemorySessionStore, SessionManager
    from core.agent_harness.session_goal.goal import SessionGoal, attach_session_goal
    from core.agent_harness.session_goal.persist import SESSION_GOAL_STATE_CUSTOM_TYPE
    from core.agent_harness.spi.cancel import HostCancelEvent, HostCancelReason
    from infrastructure.turn_host.session_lock import session_execution_lock
    from surfaces.interactive_shell.controller import InteractiveShellController
    from surfaces.interactive_shell.runtime.session_shutdown import close_repl_session
    from surfaces.interactive_shell.runtime.turn_host import run_agent_turn_queue
    from surfaces.interactive_shell.session import Session

    store = InMemorySessionStore()
    session = Session(store=store)
    store.open_session(session)
    store.append_turn(session, "chat", "seed")
    attach_session_goal(
        session,
        SessionGoal(condition="keep going", max_outer_turns=4),
    )
    SessionManager.for_session(session).flush(session)
    controller = InteractiveShellController(session)
    lease_acquired = threading.Event()
    release_lease = threading.Event()
    turn_started = asyncio.Event()
    boundary_started = asyncio.Event()

    def _hold_worker_lease() -> None:
        with session_execution_lock(session.session_id):
            lease_acquired.set()
            release_lease.wait()

    async def _run_turn(_text: str) -> None:
        turn_cancel = controller.state.current_cancel_event
        assert isinstance(turn_cancel, HostCancelEvent)
        turn_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            controller.state.finish_dispatch(turn_cancel)

    async def _on_goal_control(reason: HostCancelReason) -> None:
        boundary_started.set()
        await controller._apply_goal_control_at_turn_boundary(reason)

    lease_worker = threading.Thread(target=_hold_worker_lease)
    lease_worker.start()
    await asyncio.to_thread(lease_acquired.wait)
    queue_worker = asyncio.create_task(
        run_agent_turn_queue(
            state=controller.state,
            run_turn=_run_turn,
            on_goal_control=_on_goal_control,
        )
    )
    try:
        await controller.state.queue.put("current goal turn")
        await turn_started.wait()
        cancel = controller.state.current_cancel_event
        assert isinstance(cancel, HostCancelEvent)
        controller.state.request_goal_control(HostCancelReason.GOAL_PAUSE)
        controller.state.request_exit()
        controller.state.cancel_current_dispatch()
        await boundary_started.wait()

        queue_worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await queue_worker

        assert controller.state.requested_goal_control() is HostCancelReason.GOAL_PAUSE
        release_lease.set()
        await asyncio.to_thread(lease_worker.join, 1)
        assert not lease_worker.is_alive()
        await asyncio.to_thread(close_repl_session, session, controller.state)

        records = [
            record
            for record in store.read(session.session_id)
            if record.get("type") == "custom_message"
            and record.get("custom_type") == SESSION_GOAL_STATE_CUSTOM_TYPE
        ]
        assert records[-1].get("content", {}).get("session_goal", {}).get("status") == "paused"
    finally:
        release_lease.set()
        lease_worker.join(timeout=1)
        if not queue_worker.done():
            queue_worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await queue_worker


def test_shutdown_persists_a_pending_goal_clear() -> None:
    from core.agent_harness.session import InMemorySessionStore, SessionManager
    from core.agent_harness.session_goal.goal import SessionGoal, attach_session_goal
    from core.agent_harness.session_goal.persist import SESSION_GOAL_STATE_CUSTOM_TYPE
    from core.agent_harness.spi.cancel import HostCancelEvent, HostCancelReason
    from surfaces.interactive_shell.runtime.core.state import ReplState
    from surfaces.interactive_shell.runtime.session_shutdown import close_repl_session
    from surfaces.interactive_shell.session import Session

    store = InMemorySessionStore()
    session = Session(store=store)
    store.open_session(session)
    store.append_turn(session, "chat", "seed")
    attach_session_goal(
        session,
        SessionGoal(condition="keep going", max_outer_turns=4),
    )
    SessionManager.for_session(session).flush(session)
    state = ReplState()
    cancel = HostCancelEvent()
    cancel.request(HostCancelReason.GOAL_CLEAR)
    state.attach_cancel_event(cancel)
    state.request_exit()

    close_repl_session(session, state)

    records = [
        record
        for record in store.read(session.session_id)
        if record.get("type") == "custom_message"
        and record.get("custom_type") == SESSION_GOAL_STATE_CUSTOM_TYPE
    ]
    assert records[-1].get("content", {}).get("session_goal") is None


def test_shutdown_does_not_wait_for_a_detached_turn_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import surfaces.interactive_shell.runtime.session_shutdown as session_shutdown
    from surfaces.interactive_shell.runtime.core.state import ReplState
    from surfaces.interactive_shell.session import Session

    def _unexpected_lock(*_args: object, **_kwargs: object) -> None:
        pytest.fail("detached turn teardown must not wait for the session lease")

    state = ReplState()
    state.mark_turn_worker_detached()
    monkeypatch.setattr(session_shutdown, "session_execution_lock", _unexpected_lock)

    session_shutdown.close_repl_session(Session(), state)


def test_deferred_shutdown_does_not_apply_a_stale_control_to_a_replacement_goal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import nullcontext

    import surfaces.interactive_shell.runtime.session_shutdown as session_shutdown
    from core.agent_harness.session_goal.goal import SessionGoal, attach_session_goal
    from core.agent_harness.spi.cancel import HostCancelReason
    from surfaces.interactive_shell.session import Session

    target = SessionGoal(condition="old goal", started_at=1.0)
    replacement = SessionGoal(condition="replacement goal", started_at=2.0)
    session = Session()
    attach_session_goal(session, target)
    events: list[str] = []

    class _Manager:
        def refresh_from_storage(self, refreshed: Session) -> None:
            events.append("refresh")
            attach_session_goal(refreshed, replacement)

        def close(self, _session: Session) -> None:
            events.append("close")

    manager = _Manager()
    monkeypatch.setattr(
        session_shutdown.SessionManager,
        "for_session",
        lambda _session: manager,
    )
    monkeypatch.setattr(
        session_shutdown,
        "session_execution_lock",
        lambda _session_id: nullcontext(),
    )

    session_shutdown.close_repl_session_after_detached_worker(
        session,
        HostCancelReason.GOAL_CLEAR,
        None,
    )

    assert session.session_goal is replacement
    assert events == ["refresh", "close"]


def test_deferred_shutdown_applies_fallback_control_to_the_workers_final_goal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import nullcontext

    import surfaces.interactive_shell.runtime.session_shutdown as session_shutdown
    from core.agent_harness.session_goal.goal import SessionGoal, attach_session_goal
    from core.agent_harness.spi.cancel import HostCancelReason
    from surfaces.interactive_shell.session import Session

    worker_goal = SessionGoal(condition="late worker goal", started_at=1.0)
    session = Session()
    attach_session_goal(session, worker_goal)
    events: list[str] = []

    class _Manager:
        def refresh_from_storage(self, _session: Session) -> None:
            events.append("refresh")

        def close(self, closing: Session) -> None:
            assert closing.session_goal is None
            events.append("close")

    manager = _Manager()
    monkeypatch.setattr(
        session_shutdown.SessionManager,
        "for_session",
        lambda _session: manager,
    )
    monkeypatch.setattr(
        session_shutdown,
        "session_execution_lock",
        lambda _session_id: nullcontext(),
    )

    session_shutdown.close_repl_session_after_detached_worker(
        session,
        HostCancelReason.GOAL_CLEAR,
        None,
    )

    assert events == ["refresh", "close"]


def test_deferred_shutdown_records_exit_after_refresh_and_before_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import nullcontext

    import surfaces.interactive_shell.runtime.session_shutdown as session_shutdown
    from surfaces.interactive_shell.session import Session

    session = Session()
    events: list[str] = []

    class _Manager:
        def refresh_from_storage(self, _session: Session) -> None:
            events.append("refresh")

        def close(self, closing: Session) -> None:
            assert closing.history[-1]["type"] == "slash"
            assert closing.history[-1]["text"] == "/exit"
            events.append("close")

    manager = _Manager()
    monkeypatch.setattr(
        session_shutdown.SessionManager,
        "for_session",
        lambda _session: manager,
    )
    monkeypatch.setattr(
        session_shutdown,
        "session_execution_lock",
        lambda _session_id: nullcontext(),
    )

    session_shutdown.close_repl_session_after_detached_worker(
        session,
        None,
        "/exit",
    )

    assert events == ["refresh", "close"]


@pytest.mark.asyncio
async def test_inflight_goal_pause_is_retained_until_the_turn_attaches_a_goal() -> None:
    import asyncio

    from core.agent_harness.spi.cancel import HostCancelEvent, HostCancelReason

    controller = _controller()

    async def _hold() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(_hold())
    controller.state.attach_turn_task(task)
    cancel = controller.state.current_cancel_event
    assert isinstance(cancel, HostCancelEvent)
    try:
        kept = await controller._handle_input_action(
            RunInflightControl(
                control=InflightControl.PAUSE_GOAL,
                submitted_text="/goal pause",
            )
        )

        assert kept is True
        assert cancel.reason is HostCancelReason.GOAL_PAUSE
        assert cancel.is_set() is False
        assert not task.cancelled()
        assert await controller.state.queue.get() == "/goal pause"
        controller.state.queue.task_done()
    finally:
        task.cancel()
        _ = await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_running_dispatch_keeps_a_completed_plan() -> None:
    """A still-running task keeps its plan even after every step is done."""
    import asyncio
    import contextlib
    import threading

    controller = _controller()
    plan = _plan("completed", "completed")
    controller.session.task_plan = plan

    async def _hold() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(_hold())
    controller.state.start_dispatch(task=task, cancel_event=threading.Event())
    try:
        kept = await controller._handle_input_action(SubmitTurn(text="queued follow-up"))
        assert kept is True
        assert controller.session.task_plan is plan
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            _ = await task

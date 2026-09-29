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
    CancelTurn,
    CloseShell,
    DeliverConfirmation,
    IgnoreInput,
    PauseGoal,
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
    needs_exclusive_stdin: bool = False,
) -> object:
    return decide_input_action(
        event,
        ShellInputSnapshot(
            exit_requested=exit_requested,
            dispatch_running=dispatch_running,
            awaiting_confirmation=awaiting_confirmation,
        ),
        needs_exclusive_stdin=lambda _text: needs_exclusive_stdin,
    )


def test_decide_closes_on_input_closed() -> None:
    assert _decide(InputClosed()) == CloseShell()


def test_decide_cancels_on_input_cancelled() -> None:
    assert _decide(InputCancelled()) == CancelTurn()


@pytest.mark.parametrize("text", ["", "   "])
def test_decide_ignores_empty_or_blank_submissions(text: str) -> None:
    assert _decide(InputSubmitted(text)) == IgnoreInput()


def test_decide_ignores_submitted_input_after_exit_requested() -> None:
    assert _decide(InputSubmitted("/status"), exit_requested=True) == IgnoreInput()


def test_decide_cancels_when_cancel_request_is_typed_during_dispatch() -> None:
    assert _decide(InputSubmitted(" /cancel "), dispatch_running=True) == CancelTurn(
        submitted_text="/cancel"
    )


def test_decide_routes_goal_pause_as_an_inflight_control() -> None:
    action = _decide(
        InputSubmitted(" /goal pause "),
        dispatch_running=True,
        needs_exclusive_stdin=True,
    )

    assert action == PauseGoal(submitted_text="/goal pause")


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
        kept = await controller._handle_input_action(CancelTurn())

        assert kept is True
        assert cancel_event.is_set()
        assert controller.session.task_plan is plan
        assert controller.session.active_skill == "scheduling-github-ci-repairs"
    finally:
        task.cancel()
        _ = await asyncio.gather(task, return_exceptions=True)


def test_requesting_goal_pause_soft_cancels_the_running_turn() -> None:
    import asyncio

    from core.agent_harness.spi.cancel import HostCancelEvent
    from surfaces.interactive_shell.runtime.core.state import ReplState

    async def _scenario() -> None:
        state = ReplState()
        cancel_event = HostCancelEvent()

        async def _hold() -> None:
            await asyncio.Event().wait()

        task = asyncio.create_task(_hold())
        state.start_dispatch(task=task, cancel_event=cancel_event)
        try:
            state.request_goal_pause()

            assert state.is_goal_pause_requested()
            assert cancel_event.is_set()
            assert not task.cancelled()
        finally:
            task.cancel()
            _ = await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_scenario())


def test_inflight_goal_pause_keeps_input_open_and_does_not_leak_to_queued_turns() -> None:
    import asyncio

    from core.agent_harness.session_goal.goal import (
        SessionGoal,
        SessionGoalStatus,
        attach_session_goal,
    )
    from core.agent_harness.spi.cancel import HostCancelEvent
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
                pause_seen.append(controller.state.is_goal_pause_requested())
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
                on_goal_pause=controller._apply_goal_pause_at_turn_boundary,
            )
        )
        try:
            await controller.state.queue.put("current goal turn")
            await started.wait()
            await controller.state.queue.put("earlier queued turn")

            kept = await controller._handle_input_action(PauseGoal(submitted_text="/goal pause"))

            assert kept is True
            assert controller.session.terminal.pending_inflight_goal_pauses == 1
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
            on_goal_pause=controller._apply_goal_pause_at_turn_boundary,
        )
    )

    try:
        await controller.state.queue.put("current goal turn")
        await dispatch_finished.wait()
        await controller.state.queue.put("earlier queued turn")
        assert controller.state.is_dispatch_running()
        assert controller.state.current_cancel_event is None

        kept = await controller._handle_input_action(PauseGoal(submitted_text="/goal pause"))

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
        pause_task = asyncio.create_task(controller._apply_goal_pause_at_turn_boundary())
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
        await controller._apply_goal_pause_at_turn_boundary()

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
    from core.agent_harness.spi.cancel import HostCancelEvent
    from infrastructure.turn_host.session_lock import session_execution_lock
    from surfaces.interactive_shell.controller import InteractiveShellController
    from surfaces.interactive_shell.main import _close_repl_session
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

    async def _on_goal_pause() -> None:
        boundary_started.set()
        await controller._apply_goal_pause_at_turn_boundary()

    lease_worker = threading.Thread(target=_hold_worker_lease)
    lease_worker.start()
    await asyncio.to_thread(lease_acquired.wait)
    queue_worker = asyncio.create_task(
        run_agent_turn_queue(
            state=controller.state,
            run_turn=_run_turn,
            on_goal_pause=_on_goal_pause,
        )
    )
    try:
        await controller.state.queue.put("current goal turn")
        await turn_started.wait()
        cancel = controller.state.current_cancel_event
        assert isinstance(cancel, HostCancelEvent)
        controller.state.request_goal_pause()
        controller.state.request_exit()
        controller.state.cancel_current_dispatch()
        await boundary_started.wait()

        queue_worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await queue_worker

        assert controller.state.is_goal_pause_requested()
        release_lease.set()
        await asyncio.to_thread(lease_worker.join, 1)
        assert not lease_worker.is_alive()
        await asyncio.to_thread(_close_repl_session, session, controller.state)

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
        kept = await controller._handle_input_action(PauseGoal(submitted_text="/goal pause"))

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

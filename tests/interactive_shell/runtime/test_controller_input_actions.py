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

    kept = await controller._handle_input_action(SubmitTurn(text="new question"))

    assert kept is True
    assert controller.session.task_plan is None


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

from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

from gateway.core.middleware.active_turns import ActiveTurnRegistry
from gateway.core.middleware.approvals import MAX_APPROVAL_WAIT_SECONDS, ApprovalBroker
from gateway.transports.telegram import background
from gateway.transports.telegram.background import start_telegram_gateway_background
from gateway.transports.telegram.poller.poller import TelegramPollResult
from gateway.transports.telegram.runtime import (
    TelegramPollingRuntime,
    initialize_telegram_polling_runtime,
    shutdown_telegram_polling_runtime,
)
from gateway.transports.telegram.settings import GatewaySettings, TelegramInboundMessage

LOGGER = logging.getLogger("gateway.test")


@patch("gateway.transports.telegram.background.TelegramPoller")
def test_start_starts_poll_thread(mock_poller_cls: MagicMock) -> None:
    mock_poller_cls.return_value.poll_once.return_value = TelegramPollResult()
    handle = start_telegram_gateway_background(
        settings=GatewaySettings(bot_token="tok"),
        logger=LOGGER,
        initialize_runtime=initialize_telegram_polling_runtime,
        shutdown_runtime=shutdown_telegram_polling_runtime,
        handle_callback_to_gateway_agent=lambda *_args: None,
    )
    assert handle is not None
    handle.stop(timeout=1.0)
    mock_poller_cls.assert_called_once_with("tok")


def _message(text: str = "hello", *, chat_id: str = "chat-1") -> TelegramInboundMessage:
    return TelegramInboundMessage(
        update_id=1,
        user_id="user-1",
        chat_id=chat_id,
        message_id="msg-1",
        text=text,
    )


def _resources() -> TelegramPollingRuntime:
    return TelegramPollingRuntime(
        client=MagicMock(),
        bindings=MagicMock(),
        session_resolver=MagicMock(),
        chat_locks={},
        executor=MagicMock(),
        approvals=ApprovalBroker(),
        active_cancels=ActiveTurnRegistry(),
    )


class _FakePoller:
    """Serves scripted batches, then empty ones, counting every call."""

    def __init__(self, batches: list[TelegramPollResult]) -> None:
        self._batches = list(batches)
        self.calls = 0

    def poll_once(self) -> TelegramPollResult:
        self.calls += 1
        if self._batches:
            return self._batches.pop(0)
        # Real getUpdates long-polls; keep the idle loop off a hot spin.
        time.sleep(0.01)
        return TelegramPollResult()


def _run_loop(
    *,
    resources: TelegramPollingRuntime,
    stop_event: threading.Event,
    shutdown_drain_seconds: float = 2.0,
) -> asyncio.Task[None]:
    return asyncio.create_task(
        background._poll_telegram_until_stopped(
            settings=GatewaySettings(
                bot_token="tok", shutdown_drain_seconds=shutdown_drain_seconds
            ),
            stop_event=stop_event,
            logger=LOGGER,
            resources=resources,
            handle_callback_to_gateway_agent=lambda *_args: None,
        )
    )


async def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_polling_continues_while_a_turn_is_in_flight() -> None:
    """Regression: a turn awaiting approval must not freeze the poll loop.

    The click that resolves an approval only ever arrives on a *later*
    ``poll_once``. A loop that awaits the turn inline is still suspended on
    that turn, so it can never reach the poll that would deliver the click —
    the request always expires to denied no matter how fast the user clicks.
    """
    # Arrange
    poller = _FakePoller([TelegramPollResult(messages=[_message()])])
    turn_started = asyncio.Event()
    release_turn = asyncio.Event()

    async def _turn_blocked_on_approval(_event, **_kwargs) -> None:
        turn_started.set()
        await release_turn.wait()

    stop_event = threading.Event()

    with (
        patch.object(background, "TelegramPoller", lambda _token: poller),
        patch.object(
            background,
            "handle_polled_inbound_telegram_message",
            _turn_blocked_on_approval,
        ),
    ):
        loop_task = _run_loop(resources=_resources(), stop_event=stop_event)

        # Act
        await asyncio.wait_for(turn_started.wait(), timeout=2.0)
        polls_when_turn_began = poller.calls
        await _wait_until(lambda: poller.calls > polls_when_turn_began)

        # Assert — the loop polled again while the turn was still blocked.
        assert poller.calls > polls_when_turn_began
        assert not release_turn.is_set()  # the turn never unblocked itself

        stop_event.set()
        release_turn.set()
        await asyncio.wait_for(loop_task, timeout=2.0)


@pytest.mark.asyncio
async def test_a_failing_turn_does_not_stop_polling() -> None:
    """A detached turn's exception is logged, never left to kill the loop."""
    # Arrange
    poller = _FakePoller([TelegramPollResult(messages=[_message()])])

    async def _exploding_turn(_event, **_kwargs) -> None:
        msg = "turn blew up"
        raise RuntimeError(msg)

    stop_event = threading.Event()

    with (
        patch.object(background, "TelegramPoller", lambda _token: poller),
        patch.object(background, "handle_polled_inbound_telegram_message", _exploding_turn),
    ):
        loop_task = _run_loop(resources=_resources(), stop_event=stop_event)

        # Act
        await _wait_until(lambda: poller.calls >= 3)

        # Assert
        assert not loop_task.done()
        stop_event.set()
        await asyncio.wait_for(loop_task, timeout=2.0)
        assert loop_task.exception() is None


@pytest.mark.asyncio
async def test_shutdown_cancels_the_turn_body_not_only_its_await() -> None:
    """A turn that outlives the drain budget gets its cancel Event set.

    Cancelling the task only unwinds the ``await``; the turn body runs on the
    executor and never sees that. It stops when its ``turn_cancel`` Event is
    set, and only then does ``executor.shutdown(wait=True)`` return inside the
    shutdown budget instead of blocking to the turn timeout.
    """
    # Arrange
    poller = _FakePoller([TelegramPollResult(messages=[_message()])])
    turn_started = asyncio.Event()
    seen: list[threading.Event] = []

    async def _turn_outliving_the_budget(_event, *, turn_cancel=None, **_kwargs) -> None:
        seen.append(turn_cancel)
        turn_started.set()
        await asyncio.sleep(30)

    stop_event = threading.Event()

    with (
        patch.object(background, "TelegramPoller", lambda _token: poller),
        patch.object(
            background, "handle_polled_inbound_telegram_message", _turn_outliving_the_budget
        ),
    ):
        loop_task = _run_loop(
            resources=_resources(), stop_event=stop_event, shutdown_drain_seconds=0.05
        )
        await asyncio.wait_for(turn_started.wait(), timeout=2.0)

        # Act — shut down while the turn is still running.
        stop_event.set()
        await asyncio.wait_for(loop_task, timeout=3.0)

    # Assert
    assert seen and seen[0] is not None
    assert seen[0].is_set(), "drain cancelled the await but left the turn body running"


@pytest.mark.asyncio
async def test_stop_command_is_handled_without_dispatching_a_turn() -> None:
    """`/stop` still resolves through the registry, never as a new turn."""
    # Arrange
    poller = _FakePoller([TelegramPollResult(messages=[_message("/stop")])])
    dispatched = False

    async def _turn(_event, **_kwargs) -> None:
        nonlocal dispatched
        dispatched = True

    resources = _resources()
    stop_event = threading.Event()

    with (
        patch.object(background, "TelegramPoller", lambda _token: poller),
        patch.object(background, "handle_polled_inbound_telegram_message", _turn),
    ):
        loop_task = _run_loop(resources=resources, stop_event=stop_event)

        # Act
        await _wait_until(lambda: poller.calls >= 2)
        stop_event.set()
        await asyncio.wait_for(loop_task, timeout=2.0)

    # Assert
    assert dispatched is False
    resources.client.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_stop_in_same_batch_cancels_the_turn_dispatched_before_it() -> None:
    """Regression: `/stop` must find a turn dispatched earlier in the same batch.

    Dispatch is detached, so the turn has not reached its own registration by
    the time `/stop` is read. Unless the cancel Event is registered at dispatch,
    `/stop` reports "no active turn" and the turn it meant to stop runs on.
    """
    # Arrange
    poller = _FakePoller([TelegramPollResult(messages=[_message("hello"), _message("/stop")])])
    started = asyncio.Event()
    cancel_seen: list[bool] = []

    async def _turn(_event, *, turn_cancel=None, **_kwargs) -> None:
        cancel_seen.append(turn_cancel is not None and turn_cancel.is_set())
        started.set()

    resources = _resources()
    stop_event = threading.Event()

    with (
        patch.object(background, "TelegramPoller", lambda _token: poller),
        patch.object(background, "handle_polled_inbound_telegram_message", _turn),
    ):
        loop_task = _run_loop(resources=resources, stop_event=stop_event)

        # Act
        await asyncio.wait_for(started.wait(), timeout=2.0)
        stop_event.set()
        await asyncio.wait_for(loop_task, timeout=2.0)

    # Assert — /stop found the turn, so no "nothing to stop" reply went out,
    # and the turn itself observed the cancellation.
    assert resources.client.send_message.call_args_list == []
    assert cancel_seen == [True]


@pytest.mark.asyncio
async def test_shutdown_releases_a_turn_blocked_on_an_approval() -> None:
    """A turn parked in ``ApprovalBroker.wait`` must be denied, not left to expire.

    Setting the cancel Event does not reach a thread already blocked inside
    ``wait``: polling has stopped, so no click can arrive, and the executor
    thread would sit there for ``MAX_APPROVAL_WAIT_SECONDS`` while
    ``executor.shutdown(wait=True)`` blocks far past the stop budget.
    """
    # Arrange — a real executor and a real broker; an asyncio-level fake turn
    # would not hold the thread that the bug leaks.
    poller = _FakePoller([TelegramPollResult(messages=[_message()])])
    resources = _resources()
    executor = ThreadPoolExecutor(max_workers=1)
    resources.executor = executor
    waiting = threading.Event()
    decisions: list[tuple[bool, str]] = []

    def _block_on_approval(broker: ApprovalBroker) -> None:
        approval_id = broker.create(platform="telegram", chat_id="chat-1")
        waiting.set()
        decisions.append(broker.wait(approval_id, timeout=MAX_APPROVAL_WAIT_SECONDS))

    async def _turn_awaiting_approval(_event, *, approvals, loop, **_kwargs) -> None:
        await loop.run_in_executor(executor, _block_on_approval, approvals)

    stop_event = threading.Event()

    try:
        with (
            patch.object(background, "TelegramPoller", lambda _token: poller),
            patch.object(
                background, "handle_polled_inbound_telegram_message", _turn_awaiting_approval
            ),
        ):
            loop_task = _run_loop(
                resources=resources, stop_event=stop_event, shutdown_drain_seconds=2.0
            )
            await _wait_until(waiting.is_set)

            # Act — shut down while the turn is blocked on the approval.
            stop_event.set()
            await asyncio.wait_for(loop_task, timeout=5.0)
            # Snapshot before the teardown below, which would otherwise hand
            # the waiter the very denial this test is asserting the drain sent.
            decided_during_shutdown = list(decisions)
    finally:
        # Release the thread even if the drain left it blocked, so a failure
        # reports in seconds instead of hanging the suite for the full expiry.
        resources.approvals.close()
        executor.shutdown(wait=False)

    # Assert — the turn came back with a denial it can report, inside the drain.
    assert decided_during_shutdown == [(False, "")]

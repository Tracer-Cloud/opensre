from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from gateway.core.middleware.active_turns import ActiveTurnRegistry
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.transports.buzz import background
from gateway.transports.buzz.pending_approvals import PendingApprovals
from gateway.transports.buzz.runtime import BuzzPollingRuntime
from gateway.transports.buzz.settings import BuzzInboundMessage, GatewaySettings

REQUESTER = "a" * 64
OUTSIDER = "f" * 64
CHANNEL = "chan-1"


def _resources() -> BuzzPollingRuntime:
    return BuzzPollingRuntime(
        client=MagicMock(),
        bindings=MagicMock(),
        session_resolver=MagicMock(),
        chat_locks={},
        executor=MagicMock(),
    )


def _pending(resources: BuzzPollingRuntime) -> None:
    resources.pending_approvals.register(
        "prompt1",
        approval_id="approval-id-1",
        requester_pubkey=REQUESTER,
        channel_id=CHANNEL,
    )


def _reply(
    *, pubkey: str = REQUESTER, channel_id: str = CHANNEL, content: str = "approve"
) -> BuzzInboundMessage:
    return BuzzInboundMessage(
        event_id="reply1",
        pubkey=pubkey,
        channel_id=channel_id,
        content=content,
        created_at=100,
        reply_event_ids=frozenset({"prompt1"}),
    )


def _settings() -> GatewaySettings:
    return GatewaySettings(private_key="k", allowed_pubkeys=[REQUESTER])


def _still_pending(resources: BuzzPollingRuntime) -> bool:
    return resources.pending_approvals.find(frozenset({"prompt1"})) is not None


def test_authorized_requester_reply_resolves_the_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = _resources()
    _pending(resources)
    monkeypatch.setattr(background, "is_pubkey_authorized", lambda **_kw: True)
    resolve = MagicMock()
    monkeypatch.setattr(resources.approvals, "resolve", resolve)

    consumed = background._resolve_if_approval_reply(_reply(), resources, _settings())

    assert consumed is True
    resolve.assert_called_once_with("approval-id-1", approved=True, decided_by=REQUESTER)
    assert not _still_pending(resources)


def test_unauthorized_reply_does_not_resolve_the_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A participant outside the identity policy must not decide a write action."""
    resources = _resources()
    _pending(resources)
    monkeypatch.setattr(background, "is_pubkey_authorized", lambda **_kw: False)

    consumed = background._resolve_if_approval_reply(
        _reply(pubkey=OUTSIDER), resources, _settings()
    )

    assert consumed is True  # never falls through to a chat turn
    # Not burned — the rightful responder can still answer.
    assert _still_pending(resources)


def test_another_authorized_member_cannot_answer_someone_elses_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing the general allowlist is not authority over another member's request."""
    resources = _resources()
    _pending(resources)
    monkeypatch.setattr(background, "is_pubkey_authorized", lambda **_kw: True)
    resolve = MagicMock()
    monkeypatch.setattr(resources.approvals, "resolve", resolve)

    consumed = background._resolve_if_approval_reply(
        _reply(pubkey=OUTSIDER), resources, _settings()
    )

    assert consumed is True
    resolve.assert_not_called()
    assert _still_pending(resources)


def test_reply_from_another_channel_cannot_answer_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = _resources()
    _pending(resources)
    monkeypatch.setattr(background, "is_pubkey_authorized", lambda **_kw: True)
    resolve = MagicMock()
    monkeypatch.setattr(resources.approvals, "resolve", resolve)

    consumed = background._resolve_if_approval_reply(
        _reply(channel_id="chan-2"), resources, _settings()
    )

    assert consumed is True
    resolve.assert_not_called()
    assert _still_pending(resources)


def test_reply_that_is_not_a_decision_leaves_the_prompt_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "hold on" must not be read as a denial."""
    resources = _resources()
    _pending(resources)
    monkeypatch.setattr(background, "is_pubkey_authorized", lambda **_kw: True)
    resolve = MagicMock()
    monkeypatch.setattr(resources.approvals, "resolve", resolve)

    consumed = background._resolve_if_approval_reply(
        _reply(content="hold on, checking"), resources, _settings()
    )

    assert consumed is True
    resolve.assert_not_called()
    assert _still_pending(resources)


def test_dispatch_acknowledges_a_failed_turn() -> None:
    """A turn that reliably crashes must not be redelivered on every poll."""
    acked: list[BuzzInboundMessage] = []
    event = _reply()

    async def _raising_dispatch(_event: object, **_kw: object) -> None:
        raise RuntimeError("boom")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(background, "handle_polled_inbound_buzz_message", _raising_dispatch)
        asyncio.run(_run_dispatch(event, acknowledge=acked.append))

    assert acked == [event]


def test_cancelled_turn_is_not_acknowledged() -> None:
    """Shutdown mid-turn must leave the cursor behind so the mention replays."""
    acked: list[BuzzInboundMessage] = []
    event = _reply()
    started = asyncio.Event()

    async def _hanging_dispatch(_event: object, **_kw: object) -> None:
        started.set()
        await asyncio.Event().wait()

    async def _run() -> None:
        task = asyncio.get_running_loop().create_task(
            _dispatch_coroutine(event, acknowledge=acked.append)
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        outcomes = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(outcomes[0], asyncio.CancelledError)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(background, "handle_polled_inbound_buzz_message", _hanging_dispatch)
        asyncio.run(_run())

    assert acked == []


def _dispatch_coroutine(event: BuzzInboundMessage, *, acknowledge: object) -> object:
    return background._dispatch_turn(  # type: ignore[arg-type]
        event,
        client=MagicMock(),
        session_resolver=MagicMock(),
        settings=MagicMock(),
        executor=MagicMock(),
        chat_locks={},
        turn_semaphore=asyncio.Semaphore(4),
        approvals=ApprovalBroker(),
        pending_approvals=PendingApprovals(),
        active_cancels=ActiveTurnRegistry(),
        loop=MagicMock(),
        handle_callback_to_gateway_agent=MagicMock(),
        logger=MagicMock(),
        acknowledge=acknowledge,
    )


async def _run_dispatch(event: BuzzInboundMessage, *, acknowledge: object) -> None:
    await _dispatch_coroutine(event, acknowledge=acknowledge)  # type: ignore[misc]


def test_shutdown_denies_pending_approvals_before_draining() -> None:
    """A turn parked in ApprovalBroker.wait must not outlive the drain budget."""
    resources = _resources()
    approval_id = resources.approvals.create(platform="buzz", chat_id=CHANNEL)
    resources.pending_approvals.register(
        "prompt1",
        approval_id=approval_id,
        requester_pubkey=REQUESTER,
        channel_id=CHANNEL,
    )
    settings = GatewaySettings(private_key="k", shutdown_drain_seconds=0.05)
    waited: list[tuple[bool, str]] = []

    async def _run() -> None:
        # Stands in for the turn thread blocked on the approval.
        waiter = asyncio.get_running_loop().run_in_executor(
            None, lambda: resources.approvals.wait(approval_id, timeout=30)
        )
        await background._drain_active_turns(
            set(), resources=resources, settings=settings, logger=MagicMock(), poller=MagicMock()
        )
        waited.append(await asyncio.wait_for(waiter, timeout=1))

    asyncio.run(_run())

    assert waited == [(False, "")]
    assert not _still_pending(resources)


def test_turn_finishing_under_a_cancelled_await_is_still_acknowledged() -> None:
    """Shutdown cancels the await, not the executor thread — finished work must not replay."""
    acked: list[BuzzInboundMessage] = []
    event = _reply()
    handed_off = asyncio.Event()

    async def _completes_then_hangs(_event: object, **kw: object) -> None:
        # Stands in for run_in_executor: the turn body runs to completion on
        # its own thread (side effects landed) while the await never returns.
        on_handled = kw["on_handled"]
        assert callable(on_handled)
        on_handled()
        handed_off.set()
        await asyncio.Event().wait()

    async def _run() -> None:
        task = asyncio.get_running_loop().create_task(
            _dispatch_coroutine(event, acknowledge=acked.append)
        )
        await asyncio.wait_for(handed_off.wait(), timeout=1)
        task.cancel()
        outcomes = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(outcomes[0], asyncio.CancelledError)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(background, "handle_polled_inbound_buzz_message", _completes_then_hangs)
        asyncio.run(_run())

    assert acked == [event]

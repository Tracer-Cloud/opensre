"""Tests for the Slack turn dispatcher: admit gate, auth, seeding, timeout, reactions."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from unittest.mock import patch

import pytest

from config.constants.billing import ORGANIZATION_ID_ENV, USAGE_SECRET_ENV, WEBAPP_URL_ENV
from config.constants.gateway import TURN_ERROR_MESSAGE
from config.principal import Principal, StorageScope
from gateway.core.billing import turn_metering
from gateway.core.billing.credits_client import CreditsOutcome
from gateway.tests.billing.turn_metering_harness import metered_callback
from gateway.transports.slack.processing.dispatcher import SlackTurnDispatcher
from gateway.transports.slack.processing.events import SlackInboundMessage
from gateway.transports.slack.processing.principal import slack_scope
from gateway.transports.slack.settings import SlackGatewaySettings
from infrastructure.turn_host.status_messages import user_facing_error_message


@pytest.fixture(autouse=True)
def _isolate_slack_integration_store():
    """Worker tests must not depend on the developer's ~/.opensre integrations."""
    with (
        patch("gateway.core.middleware.identity_policy.get_integration", return_value=None),
        patch("gateway.core.middleware.identity_policy.upsert_instance"),
    ):
        yield


TEST_ORG_ID = "org_test_dispatcher"


def _test_scope() -> StorageScope:
    """Scope a turn would have resolved to under the autouse silo org."""
    return slack_scope(Principal.org(TEST_ORG_ID), "U1")


@pytest.fixture(autouse=True)
def _metering_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve an owning org, but deliberately disable remote metering.

    With no webapp URL, setting only the org keeps every outcome ``DISABLED``
    while giving principal resolution a silo organization to land on. Install
    lookup is stubbed so tests do not touch the gateway SQLite catalog.
    """
    for name in (WEBAPP_URL_ENV, USAGE_SECRET_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(ORGANIZATION_ID_ENV, TEST_ORG_ID)


class _FakeMessagingClient:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.reactions: list[dict[str, str]] = []

    def post_message(
        self,
        *,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: Any = None,
    ) -> str | None:
        self.posts.append(
            {"channel": channel, "text": text, "thread_ts": thread_ts, "blocks": blocks}
        )
        return f"ts-{len(self.posts)}"

    def update_message(self, *, channel: str, ts: str, text: str, blocks: Any = None) -> bool:
        self.updates.append({"channel": channel, "ts": ts, "text": text, "blocks": blocks})
        return True

    def add_reaction(self, *, channel: str, timestamp: str, emoji: str) -> bool:
        self.reactions.append(
            {"op": "add", "channel": channel, "timestamp": timestamp, "emoji": emoji}
        )
        return True

    def remove_reaction(self, *, channel: str, timestamp: str, emoji: str) -> bool:
        self.reactions.append(
            {"op": "remove", "channel": channel, "timestamp": timestamp, "emoji": emoji}
        )
        return True


class _FakeSession:
    session_id = "session-12345678"


class _FakeSessionResolver:
    def __init__(self, *, has_session: bool = True) -> None:
        self.calls: list[dict[str, str]] = []
        self._has_session = has_session

    def resolve(
        self,
        *,
        user_id: str,
        chat_id: str,
        principal: object = None,
        actor: object = None,
    ) -> _FakeSession:
        self.calls.append({"user_id": user_id, "chat_id": chat_id})
        _ = principal, actor
        return _FakeSession()

    def has_conversation(self, *, conversation_key: str, principal: object = None) -> bool:
        _ = conversation_key, principal
        return self._has_session

    def has_session(self, *, user_id: str, principal: object = None, actor: object = None) -> bool:
        _ = user_id, principal, actor
        return self._has_session

    def rotate(
        self,
        *,
        user_id: str,
        chat_id: str,
        principal: object = None,
        actor: object = None,
    ) -> _FakeSession:
        self.calls.append({"user_id": user_id, "chat_id": chat_id, "rotate": "1"})
        _ = principal, actor
        return _FakeSession()


def _settings(
    allowed_user_ids: list[str] | None = None,
    *,
    allow_open_workspace: bool = False,
    turn_timeout_seconds: float = 240.0,
) -> SlackGatewaySettings:
    return SlackGatewaySettings(
        bot_token="xoxb-test",
        app_token="xapp-test",
        allowed_user_ids=allowed_user_ids or [],
        allow_open_workspace=allow_open_workspace,
        status_update_interval_seconds=0.01,
        turn_timeout_seconds=turn_timeout_seconds,
    )


def _inbound(
    *,
    text: str = "check the api",
    ts: str = "100.1",
    team_id: str = "T1",
    channel_id: str = "C1",
) -> SlackInboundMessage:
    return SlackInboundMessage(
        team_id=team_id,
        user_id="U1",
        channel_id=channel_id,
        ts=ts,
        thread_ts="100.1",
        text=text,
    )


def _dispatcher(
    *,
    settings: SlackGatewaySettings,
    messaging: _FakeMessagingClient,
    resolver: _FakeSessionResolver,
    handler: Any,
    bot_user_id: str = "",
    proactive: Any = None,
) -> SlackTurnDispatcher:
    return SlackTurnDispatcher(
        settings=settings,
        messaging=messaging,
        session_resolver=resolver,  # type: ignore[arg-type]
        handler=handler,
        logger=logging.getLogger("test"),
        bot_user_id=bot_user_id,
        proactive=proactive,
    )


def test_authorized_message_reaches_handler_with_thread_sink() -> None:
    messaging = _FakeMessagingClient()
    resolver = _FakeSessionResolver()
    turns: list[tuple[str, Any]] = []

    def handler(text: str, session: Any, sink: Any, _logger: logging.Logger) -> None:
        turns.append((text, session))
        sink.finalize("done")

    _dispatcher(
        settings=_settings(["U1"]), messaging=messaging, resolver=resolver, handler=handler
    ).dispatch(_inbound())

    assert len(turns) == 1
    agent_text, session = turns[0]
    assert agent_text.startswith("[Slack channel_id=C1 user=<@U1>]")
    assert "thread_ts" not in agent_text
    assert "slack_read_messages" not in agent_text
    assert agent_text.endswith("check the api")
    assert session is turns[0][1]
    assert resolver.calls == [{"user_id": "T1:C1:100.1", "chat_id": "C1"}]
    # Placeholder posted into the thread, then edited with the final answer.
    assert messaging.posts[0]["thread_ts"] == "100.1"
    assert messaging.updates[-1]["text"] == "done"
    # Coworker UX: eyes while working, then checkmark.
    emoji_ops = [(r["op"], r["emoji"]) for r in messaging.reactions]
    assert ("add", "eyes") in emoji_ops
    assert ("remove", "eyes") in emoji_ops
    assert ("add", "white_check_mark") in emoji_ops


def test_completed_turn_queues_proactive_review_for_same_actor_and_thread() -> None:
    messaging = _FakeMessagingClient()
    resolver = _FakeSessionResolver()

    class _Proactive:
        def __init__(self) -> None:
            self.enqueued: list[dict[str, Any]] = []

        def capture_boundary(self, session_id: str) -> str:
            assert session_id == _FakeSession.session_id
            return "before-record"

        def enqueue(self, **kwargs: Any) -> bool:
            self.enqueued.append(kwargs)
            return True

    proactive = _Proactive()

    def handler(_text: str, _session: Any, sink: Any, _logger: logging.Logger) -> None:
        sink.finalize("done")

    _dispatcher(
        settings=_settings(["U1"]),
        messaging=messaging,
        resolver=resolver,
        handler=handler,
        proactive=proactive,
    ).dispatch(_inbound())

    assert len(proactive.enqueued) == 1
    queued = proactive.enqueued[0]
    assert queued["session_id"] == _FakeSession.session_id
    assert queued["start_record_id"] == "before-record"
    assert queued["channel_id"] == "C1"
    assert queued["thread_ts"] == "100.1"
    assert queued["user_id"] == "U1"
    assert queued["scope"] == _test_scope()


def test_unauthorized_user_gets_denial_reply_and_no_turn() -> None:
    messaging = _FakeMessagingClient()
    resolver = _FakeSessionResolver()
    turns: list[str] = []

    _dispatcher(
        settings=_settings(["U999"]),
        messaging=messaging,
        resolver=resolver,
        handler=lambda text, *_args: turns.append(text),
    ).dispatch(_inbound())

    assert turns == []
    assert resolver.calls == []
    # Generic reply only — no user ids, allowlists, or env var names leak to the channel.
    denial = messaging.posts[0]["text"] or ""
    assert "not authorized" in denial
    assert "U1" not in denial
    assert "SLACK_" not in denial


def test_out_of_credits_blocks_turn_with_short_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    messaging = _FakeMessagingClient()
    resolver = _FakeSessionResolver()
    turns: list[str] = []
    reasons: list[str] = []

    def deny(_organization_id: str | None = None, **kwargs: Any) -> CreditsOutcome:
        reasons.append(kwargs["reason"])
        return CreditsOutcome.DENIED

    monkeypatch.setattr(turn_metering, "consume_credits", deny)

    _dispatcher(
        settings=_settings(["U1"]),
        messaging=messaging,
        resolver=resolver,
        handler=metered_callback(lambda text, *_args: turns.append(text)),
    ).dispatch(_inbound())

    assert turns == []
    assert reasons == ["slack_turn"]
    # Short thread reply; no balances or env details leak to the channel.
    assert messaging.updates[-1]["text"] == "Out of credits — top up in the OpenSRE console."
    assert messaging.posts[0]["thread_ts"] == "100.1"
    emoji_ops = [(reaction["op"], reaction["emoji"]) for reaction in messaging.reactions]
    assert ("remove", "eyes") in emoji_ops
    assert ("add", "x") in emoji_ops
    assert ("add", "white_check_mark") not in emoji_ops


def test_same_ts_in_two_channels_bills_under_two_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Slack ``ts`` is unique per channel, not per workspace, and one org can own
    # several teams and channels. Keying on ts alone would let the ledger treat
    # the second, unrelated delivery as a replay of the first and skip its debit.
    keys: list[str] = []

    def capture(*_args: Any, **kwargs: Any) -> CreditsOutcome:
        keys.append(kwargs["idempotency_key"])
        return CreditsOutcome.ALLOWED

    monkeypatch.setattr(turn_metering, "consume_credits", capture)

    def handler(_text: str, _session: Any, sink: Any, _logger: logging.Logger) -> None:
        sink.finalize("done")

    for channel_id in ("C1", "C2"):
        _dispatcher(
            settings=_settings(["U1"]),
            messaging=_FakeMessagingClient(),
            resolver=_FakeSessionResolver(),
            handler=metered_callback(handler),
        ).dispatch(_inbound(ts="100.1", channel_id=channel_id))

    first, second = keys
    assert first != second
    assert first == "slack:T1:C1:100.1"
    assert second == "slack:T1:C2:100.1"


@pytest.mark.parametrize("outcome", [CreditsOutcome.ALLOWED, CreditsOutcome.DISABLED])
def test_allowed_or_deliberately_disabled_metering_runs_the_turn(
    monkeypatch: pytest.MonkeyPatch, outcome: CreditsOutcome
) -> None:
    messaging = _FakeMessagingClient()
    resolver = _FakeSessionResolver()
    turns: list[str] = []
    monkeypatch.setattr(
        turn_metering,
        "consume_credits",
        lambda *_args, **_kw: outcome,
    )

    def handler(text: str, _session: Any, sink: Any, _logger: logging.Logger) -> None:
        turns.append(text)
        sink.finalize("done")

    _dispatcher(
        settings=_settings(["U1"]),
        messaging=messaging,
        resolver=resolver,
        handler=metered_callback(handler),
    ).dispatch(_inbound())

    assert len(turns) == 1


@pytest.mark.parametrize("outcome", [CreditsOutcome.UNCONFIGURED, CreditsOutcome.UNAVAILABLE])
def test_untrustworthy_credit_outcomes_fail_closed(
    monkeypatch: pytest.MonkeyPatch, outcome: CreditsOutcome
) -> None:
    messaging = _FakeMessagingClient()
    turns: list[str] = []
    monkeypatch.setattr(
        turn_metering,
        "consume_credits",
        lambda *_args, **_kw: outcome,
    )

    _dispatcher(
        settings=_settings(["U1"]),
        messaging=messaging,
        resolver=_FakeSessionResolver(),
        handler=metered_callback(lambda text, *_args: turns.append(text)),
    ).dispatch(_inbound())

    assert turns == []
    assert messaging.updates[-1]["text"] == user_facing_error_message(TURN_ERROR_MESSAGE)


def test_handler_exception_is_contained() -> None:
    messaging = _FakeMessagingClient()

    def handler(*_args: Any) -> None:
        raise RuntimeError("boom")

    _dispatcher(
        settings=_settings(["U1"]),
        messaging=messaging,
        resolver=_FakeSessionResolver(),
        handler=handler,
    ).dispatch(_inbound())


def test_errored_turn_replaces_placeholder_with_error() -> None:
    """A raising handler must leave a visible error in the thread, not a frozen
    'Digging in…' placeholder (only the reaction changing)."""
    messaging = _FakeMessagingClient()

    def handler(_text: str, _session: Any, _sink: Any, _logger: logging.Logger) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        _dispatcher(
            settings=_settings(["U1"]),
            messaging=messaging,
            resolver=_FakeSessionResolver(),
            handler=handler,
        )._run_turn(_inbound(), _test_scope())

    # The placeholder message was edited to an error, and the message shows ✗.
    assert messaging.updates, "placeholder was never updated on error"
    assert "went wrong" in messaging.updates[-1]["text"].lower()
    assert ("add", "x") in [(r["op"], r["emoji"]) for r in messaging.reactions]


def test_agent_context_omits_thread_ts_to_avoid_thread_reads() -> None:
    # Arrange / Act
    from gateway.transports.slack.processing.dispatcher import _agent_text_with_slack_context

    text = _agent_text_with_slack_context(_inbound())

    # Assert: channel id is present for tool targeting, but the thread ts is not
    # exposed (the agent would copy it into channel reads, returning one thread).
    assert "channel_id=C1" in text
    assert "thread_ts" not in text
    assert "check the api" in text


def test_agent_context_attributes_the_speaker() -> None:
    """The turn prefix names who is speaking (multi-user thread attribution)."""
    from gateway.transports.slack.processing.dispatcher import _agent_text_with_slack_context

    text = _agent_text_with_slack_context(_inbound())

    # Single metadata line: prefix stays one line, text follows on the next.
    assert text == "[Slack channel_id=C1 user=<@U1>]\ncheck the api"


def test_stop_with_nothing_running_posts_idle_reply() -> None:
    messaging = _FakeMessagingClient()
    turns: list[str] = []

    def handler(text: str, _session: Any, sink: Any, _logger: logging.Logger) -> None:
        turns.append(text)
        sink.finalize("done")

    _dispatcher(
        settings=_settings(["U1"]),
        messaging=messaging,
        resolver=_FakeSessionResolver(),
        handler=handler,
    ).dispatch(_inbound(text="/stop"))

    assert turns == []
    assert any("Nothing running to stop" in p["text"] for p in messaging.posts)


def test_stop_cancels_in_flight_turn() -> None:
    messaging = _FakeMessagingClient()
    started = threading.Event()
    release = threading.Event()
    seen_cancel: list[threading.Event] = []

    def hanging_handler(_text: str, _session: Any, sink: Any, _logger: logging.Logger) -> None:
        cancel = getattr(sink, "turn_cancel", None)
        assert isinstance(cancel, threading.Event)
        seen_cancel.append(cancel)
        started.set()
        release.wait(5.0)

    dispatcher = _dispatcher(
        settings=_settings(["U1"], turn_timeout_seconds=30.0),
        messaging=messaging,
        resolver=_FakeSessionResolver(),
        handler=hanging_handler,
    )
    worker = threading.Thread(target=lambda: dispatcher._run_turn(_inbound(), _test_scope()))
    worker.start()
    assert started.wait(3.0), "turn did not start"
    dispatcher.dispatch(_inbound(text="/stop", ts="100.2"))
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not any(
            update["text"] == "Stopped." for update in messaging.updates
        ):
            time.sleep(0.02)
    finally:
        release.set()
        worker.join(5.0)

    assert seen_cancel and seen_cancel[0].is_set()
    assert any(update["text"] == "Stopped." for update in messaging.updates)


def test_turn_timeout_finalizes_placeholder_when_handler_hangs() -> None:
    """A turn that outruns the timeout gets a visible message + ✗ instead of a
    frozen placeholder, even though the blocking handler cannot be cancelled."""
    messaging = _FakeMessagingClient()
    release = threading.Event()

    def hanging_handler(_text: str, _session: Any, _sink: Any, _logger: logging.Logger) -> None:
        release.wait(5.0)  # blocks past the tiny timeout; released once observed

    dispatcher = _dispatcher(
        settings=_settings(["U1"], turn_timeout_seconds=0.05),
        messaging=messaging,
        resolver=_FakeSessionResolver(),
        handler=hanging_handler,
    )
    worker = threading.Thread(target=lambda: dispatcher._run_turn(_inbound(), _test_scope()))
    worker.start()
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not any(
            "taking longer" in update["text"].lower() for update in messaging.updates
        ):
            time.sleep(0.02)
    finally:
        release.set()
        worker.join(5.0)

    assert any("taking longer" in update["text"].lower() for update in messaging.updates), (
        "timeout did not replace the placeholder"
    )
    ops = [(r["op"], r["emoji"]) for r in messaging.reactions]
    assert ("add", "x") in ops
    # The timeout owns the outcome, so a late normal completion must not stack a
    # done tick over the timeout's cross.
    assert ("add", "white_check_mark") not in ops


_BOT_ID = "UBOT"


def _untagged_reply(
    text: str = "and the second one?", ts: str = "200.2", user: str = "U1"
) -> SlackInboundMessage:
    return SlackInboundMessage(
        team_id="T1",
        user_id=user,
        channel_id="C1",
        ts=ts,
        thread_ts="100.1",
        text=text,
        addressed=False,
    )


def _gated_dispatcher(
    *,
    messaging: _FakeMessagingClient,
    handler: Any,
    has_session: bool = True,
    allowed_user_ids: list[str] | None = None,
) -> SlackTurnDispatcher:
    return _dispatcher(
        settings=_settings(allowed_user_ids or ["U1"]),
        messaging=messaging,
        resolver=_FakeSessionResolver(has_session=has_session),
        handler=handler,
        bot_user_id=_BOT_ID,
    )


def _collecting_handler(turns: list[str]) -> Any:
    def handler(text: str, _s: Any, sink: Any, _log: logging.Logger) -> None:
        turns.append(text)
        sink.finalize("ok")

    return handler


def test_untagged_reply_ignored_when_bot_not_in_thread() -> None:
    messaging = _FakeMessagingClient()
    turns: list[str] = []
    dispatcher = _gated_dispatcher(
        messaging=messaging, handler=_collecting_handler(turns), has_session=False
    )

    # Even with an open attention window, a thread without a session binding
    # (bot never joined it) is never engaged.
    dispatcher.dispatch(_inbound())
    turns.clear()
    messaging.updates.clear()
    dispatcher.dispatch(_untagged_reply())

    # No turn ran and nothing was posted — the bot stays out of threads it hasn't joined.
    assert turns == []
    assert messaging.updates == []


def test_untagged_reply_answered_inside_attention_window() -> None:
    messaging = _FakeMessagingClient()
    turns: list[str] = []
    dispatcher = _gated_dispatcher(messaging=messaging, handler=_collecting_handler(turns))

    # The mention opens the thread's attention window…
    dispatcher.dispatch(_inbound())
    turns.clear()
    # …so an un-tagged follow-up question in the same thread is answered.
    dispatcher.dispatch(_untagged_reply())

    assert len(turns) == 1
    assert turns[0].endswith("and the second one?")


def test_solo_thread_engages_every_reply_like_the_transcript() -> None:
    """One human chatting with the bot must not need @mentions per message:
    plain instructions ('please refer Lars as the greatest intern') engage."""
    messaging = _FakeMessagingClient()
    turns: list[str] = []
    dispatcher = _gated_dispatcher(messaging=messaging, handler=_collecting_handler(turns))

    dispatcher.dispatch(_inbound())  # "@bot …" opens the conversation
    turns.clear()
    dispatcher.dispatch(
        _untagged_reply(text="please refer Lars as the greatest intern", ts="200.1")
    )
    dispatcher.dispatch(_untagged_reply(text="who is the greatest intern", ts="200.2"))
    dispatcher.dispatch(_untagged_reply(text="thanks, note that down", ts="200.3"))

    # Every reply ran a turn — no question-mark heuristics, no rate limit.
    assert len(turns) == 3


def test_untagged_reply_ignored_without_prior_mention() -> None:
    """Bot in thread (binding exists) but no mention this process: stay silent."""
    messaging = _FakeMessagingClient()
    turns: list[str] = []

    _gated_dispatcher(messaging=messaging, handler=_collecting_handler(turns)).dispatch(
        _untagged_reply()
    )

    assert turns == []
    assert messaging.posts == []


def test_human_to_human_reply_passes_through_silently() -> None:
    messaging = _FakeMessagingClient()
    turns: list[str] = []
    dispatcher = _gated_dispatcher(messaging=messaging, handler=_collecting_handler(turns))

    dispatcher.dispatch(_inbound())
    turns.clear()
    messaging.posts.clear()
    # A statement aimed at another human: no affirmative, no bot name, no question.
    dispatcher.dispatch(_untagged_reply(text="<@U2> the deploy finished, take a look"))

    assert turns == []
    assert messaging.posts == []


def test_mention_copy_from_message_event_is_deduped() -> None:
    """With message.channels subscribed, a mention arrives twice; only the
    app_mention copy runs a turn."""
    messaging = _FakeMessagingClient()
    turns: list[str] = []
    dispatcher = _gated_dispatcher(messaging=messaging, handler=_collecting_handler(turns))

    dispatcher.dispatch(_inbound())
    turns.clear()
    dispatcher.dispatch(_untagged_reply(text=f"<@{_BOT_ID}> and the second one?"))

    assert turns == []


def test_unprompted_replies_rate_limited_with_eyes_ack_in_multi_user_thread() -> None:
    messaging = _FakeMessagingClient()
    turns: list[str] = []
    dispatcher = _gated_dispatcher(
        messaging=messaging,
        handler=_collecting_handler(turns),
        allowed_user_ids=["U1", "U2"],
    )

    dispatcher.dispatch(_inbound())  # U1's mention opens the window
    turns.clear()
    # A second human's questions engage under the unprompted budget only.
    for index in range(4):
        dispatcher.dispatch(
            _untagged_reply(text=f"what about attempt {index}?", ts=f"200.{index}", user="U2")
        )

    # Two unprompted turns ran; the rest were acknowledged with 👀 only.
    assert len(turns) == 2
    for rate_limited_ts in ("200.2", "200.3"):
        ops = [
            (r["op"], r["emoji"]) for r in messaging.reactions if r["timestamp"] == rate_limited_ts
        ]
        assert ops == [("add", "eyes")], f"expected 👀-only ack for {rate_limited_ts}, got {ops}"


def test_turn_is_refused_when_the_owning_principal_cannot_be_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No known owner means no turn: a turn must never read or bill a guessed principal."""
    # Arrange: no install record and no silo org, so resolution has nothing to land on.
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)
    messaging = _FakeMessagingClient()
    turns: list[str] = []
    dispatcher = _gated_dispatcher(messaging=messaging, handler=_collecting_handler(turns))

    # Act
    dispatcher.dispatch(_inbound())

    # Assert: the handler never ran and nothing was posted back to the channel.
    assert turns == []
    assert messaging.posts == []

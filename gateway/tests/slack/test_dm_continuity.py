"""Slack DM continuity: every top-level DM continues the DM's one session.

Drives raw Events API payloads through ``parse_events_api_payload`` and
``SlackTurnDispatcher.dispatch`` against a real ``SessionResolver``,
``FileBindingStore``, and JSONL session storage, so a session must survive the
same resolve → flush → resolve round trip production turns take.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from config.constants import paths
from config.constants.billing import ORGANIZATION_ID_ENV, USAGE_SECRET_ENV, WEBAPP_URL_ENV
from core.agent_harness.prompts.memory.conversation import expand_affirmative_follow_up
from core.agent_harness.session import SessionCore, SessionManager
from core.agent_harness.session.pending_offer import (
    PendingScheduleOffer,
    first_pending_offer,
    is_pending_offer_confirmation,
)
from core.agent_harness.session.persistence.jsonl_repo import JsonlSessionRepo
from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
from gateway.core.storage import FileBindingStore, SessionResolver
from gateway.transports.slack.processing.dispatcher import SlackTurnDispatcher
from gateway.transports.slack.processing.events import parse_events_api_payload
from gateway.transports.slack.processing.security import SlackInboundDecision
from gateway.transports.slack.settings import SlackGatewaySettings

_ORG = "org_dm_continuity"
_TEAM = "T_DM"
_USER = "U_DM"
_DM_CHANNEL = "D_DM"


class _FakeMessagingClient:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    def post_message(
        self,
        *,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: Any = None,
    ) -> str | None:
        self.posts.append({"channel": channel, "text": text, "thread_ts": thread_ts})
        _ = blocks
        return f"ts-{len(self.posts)}"

    def update_message(self, *, channel: str, ts: str, text: str, blocks: Any = None) -> bool:
        _ = (channel, ts, text, blocks)
        return True

    def add_reaction(self, *, channel: str, timestamp: str, emoji: str) -> bool:
        _ = (channel, timestamp, emoji)
        return True

    def remove_reaction(self, *, channel: str, timestamp: str, emoji: str) -> bool:
        _ = (channel, timestamp, emoji)
        return True


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    monkeypatch.setenv(ORGANIZATION_ID_ENV, _ORG)
    for name in (WEBAPP_URL_ENV, USAGE_SECRET_ENV):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _allow_all_security():
    decision = SlackInboundDecision(allowed=True)
    with (
        patch(
            "gateway.transports.slack.processing.dispatcher.enforce_inbound_slack_message_security",
            return_value=decision,
        ),
        patch("gateway.core.middleware.inbound_decision.persist_policy_if_needed"),
        patch(
            "gateway.transports.slack.processing.dispatcher.session_needs_thread_seed",
            return_value=False,
        ),
        patch("gateway.transports.slack.processing.dispatcher.mark_turn_working"),
        patch("gateway.transports.slack.processing.dispatcher.mark_turn_done"),
        patch("gateway.transports.slack.processing.dispatcher.mark_turn_failed"),
    ):
        yield


@pytest.fixture
def resolver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionResolver:
    monkeypatch.setattr(SessionCore, "warm_resolved_integrations", lambda _self, **_k: None)
    monkeypatch.setattr(SessionCore, "hydrate_configured_integrations", lambda _self: None)
    manager = SessionManager(store=JsonlSessionStore(), repo=JsonlSessionRepo())
    return SessionResolver(
        FileBindingStore(tmp_path / "bindings.json"), manager=manager, platform="slack"
    )


def _dispatcher(resolver: SessionResolver, handler: Any) -> SlackTurnDispatcher:
    return SlackTurnDispatcher(
        settings=SlackGatewaySettings(
            bot_token="xoxb-test",
            app_token="xapp-test",
            allowed_user_ids=[],
            allow_open_workspace=True,
            status_update_interval_seconds=0.01,
            turn_timeout_seconds=60.0,
        ),
        messaging=_FakeMessagingClient(),  # type: ignore[arg-type]
        session_resolver=resolver,
        handler=handler,
        logger=logging.getLogger("test.dm"),
    )


def _payload(
    *, ts: str, text: str, channel: str = _DM_CHANNEL, thread_ts: str = ""
) -> dict[str, Any]:
    is_dm = channel.startswith("D")
    event: dict[str, Any] = {
        "type": "message" if is_dm else "app_mention",
        "user": _USER,
        "channel": channel,
        "ts": ts,
        "text": text if is_dm else f"<@UBOT> {text}",
    }
    if is_dm:
        event["channel_type"] = "im"
    if thread_ts:
        event["thread_ts"] = thread_ts
    return {"team_id": _TEAM, "event": event}


def _send(dispatcher: SlackTurnDispatcher, **payload: Any) -> None:
    inbound = parse_events_api_payload(_payload(**payload))
    assert inbound is not None
    dispatcher.dispatch(inbound)


def test_top_level_dms_continue_one_session(resolver: SessionResolver) -> None:
    seen: list[str] = []

    def handler(_text: str, session: Any, sink: Any, _logger: logging.Logger) -> None:
        seen.append(session.session_id)
        sink.finalize("ok")

    dispatcher = _dispatcher(resolver, handler)
    _send(dispatcher, ts="100.1", text="what failed last night?")
    _send(dispatcher, ts="200.1", text="and the night before?")
    _send(dispatcher, ts="300.1", text="thanks", thread_ts="200.1")

    assert len(seen) == 3
    assert len(set(seen)) == 1


def test_yes_in_a_new_top_level_dm_confirms_the_pending_schedule_offer(
    resolver: SessionResolver,
) -> None:
    offer = PendingScheduleOffer(
        kind="manual_loop",
        cron="0 9 * * 1-5",
        timezone="UTC",
        provider="slack",
        chat_id=_DM_CHANNEL,
        prompt="summarize CI failures",
    )
    expanded: list[str] = []
    confirmed: list[bool] = []

    def handler(text: str, session: Any, sink: Any, _logger: logging.Logger) -> None:
        if not confirmed:
            # Turn 1 proposes the schedule; the turn engine flushes at turn end.
            session.record("chat", text)
            session.pending_schedule_offer = offer
            session.store.flush(session)
            confirmed.append(False)
        else:
            # Turn 2: the same expansion the orchestrator runs on a bare "yes".
            result = expand_affirmative_follow_up(
                text,
                session.cli_agent_messages,
                pending_offer=first_pending_offer(session),
            )
            expanded.append(result)
            confirmed.append(is_pending_offer_confirmation(session, result))
        sink.finalize("ok")

    dispatcher = _dispatcher(resolver, handler)
    _send(dispatcher, ts="100.1", text="email me CI failures every weekday at 9")
    _send(dispatcher, ts="200.1", text="yes")

    assert confirmed == [False, True]
    assert expanded == [offer.to_slash_command()]


def test_channel_mentions_still_get_one_session_per_thread(resolver: SessionResolver) -> None:
    seen: list[str] = []

    def handler(_text: str, session: Any, sink: Any, _logger: logging.Logger) -> None:
        seen.append(session.session_id)
        sink.finalize("ok")

    dispatcher = _dispatcher(resolver, handler)
    _send(dispatcher, ts="100.1", text="check the api", channel="C_CHAN")
    _send(dispatcher, ts="200.1", text="check the db", channel="C_CHAN")
    _send(dispatcher, ts="300.1", text="any update?", channel="C_CHAN", thread_ts="100.1")

    assert seen[0] != seen[1]
    assert seen[2] == seen[0]

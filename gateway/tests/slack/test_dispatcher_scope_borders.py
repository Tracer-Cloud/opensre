"""Slack dispatcher is the only production binder of StorageScope.

These regressions prove a live turn:
* resolves principal + actor for the speaking Slack user
* binds scope for the whole turn (paths land under the org / user)
* forwards principal + actor into SessionResolver
* bills the org principal, not the Slack user id
* refuses when no owner can be resolved (no guessed principal)
* never lets Alice's session_id leak into Bob's resolve call
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from config.constants import paths
from config.constants.billing import ORGANIZATION_ID_ENV, USAGE_SECRET_ENV, WEBAPP_URL_ENV
from config.principal import Actor, Principal
from config.scope_context import current_scope
from gateway.core.billing import turn_metering
from gateway.core.billing.credits_client import CreditsOutcome
from gateway.tests.billing.turn_metering_harness import metered_callback
from gateway.transports.slack.processing.dispatcher import SlackTurnDispatcher
from gateway.transports.slack.processing.events import SlackInboundMessage
from gateway.transports.slack.processing.principal import slack_scope
from gateway.transports.slack.settings import SlackGatewaySettings

TEST_ORG = "org_border_acme"


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    for name in (WEBAPP_URL_ENV, USAGE_SECRET_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(ORGANIZATION_ID_ENV, TEST_ORG)
    with (
        patch("gateway.core.middleware.identity_policy.get_integration", return_value=None),
        patch("gateway.core.middleware.identity_policy.upsert_instance"),
    ):
        yield tmp_path


class _Messaging:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    def post_message(
        self, *, channel: str, text: str, thread_ts: str | None = None, **_: Any
    ) -> str:
        self.posts.append({"channel": channel, "text": text, "thread_ts": thread_ts})
        return f"ts-{len(self.posts)}"

    def update_message(self, **_: Any) -> bool:
        return True

    def add_reaction(self, **_: Any) -> bool:
        return True

    def remove_reaction(self, **_: Any) -> bool:
        return True


class _Session:
    session_id = "sess-border"


class _RecordingResolver:
    def __init__(self) -> None:
        self.resolve_calls: list[dict[str, Any]] = []
        self.conversation_calls: list[dict[str, Any]] = []

    def resolve(
        self,
        *,
        user_id: str,
        chat_id: str,
        principal: object = None,
        actor: object = None,
    ) -> _Session:
        self.resolve_calls.append(
            {
                "user_id": user_id,
                "chat_id": chat_id,
                "principal": principal,
                "actor": actor,
            }
        )
        return _Session()

    def has_conversation(self, *, conversation_key: str, principal: object = None) -> bool:
        self.conversation_calls.append(
            {"conversation_key": conversation_key, "principal": principal}
        )
        return True

    def has_session(self, *, user_id: str, principal: object = None, actor: object = None) -> bool:
        _ = user_id, principal, actor
        return True

    def rotate(self, **kwargs: Any) -> _Session:
        self.resolve_calls.append({"rotate": True, **kwargs})
        return _Session()


def _inbound(*, user_id: str = "U_ALICE", text: str = "check cpu") -> SlackInboundMessage:
    return SlackInboundMessage(
        team_id="T_ACME",
        user_id=user_id,
        channel_id="C1",
        ts="100.1",
        thread_ts="100.1",
        text=text,
    )


def _dispatcher(
    *,
    resolver: _RecordingResolver,
    handler: Any,
    messaging: _Messaging | None = None,
) -> SlackTurnDispatcher:
    return SlackTurnDispatcher(
        settings=SlackGatewaySettings(
            bot_token="xoxb-test",
            app_token="xapp-test",
            allowed_user_ids=[],
            allow_open_workspace=True,
            status_update_interval_seconds=0.01,
            turn_timeout_seconds=30.0,
        ),
        messaging=messaging or _Messaging(),
        session_resolver=resolver,  # type: ignore[arg-type]
        handler=handler,
        logger=logging.getLogger("test.border"),
        bot_user_id="U_BOT",
    )


def test_dispatcher_forwards_org_principal_and_speaking_actor() -> None:
    resolver = _RecordingResolver()
    turns: list[str] = []

    def handler(text: str, session: object, sink: object, logger: object) -> None:
        _ = session, sink, logger
        turns.append(text)

    _dispatcher(resolver=resolver, handler=handler).dispatch(_inbound(user_id="U_ALICE"))

    assert len(turns) == 1
    assert turns[0].endswith("check cpu")
    assert "U_ALICE" in turns[0]
    assert len(resolver.resolve_calls) == 1
    call = resolver.resolve_calls[0]
    assert call["principal"] == Principal.org(TEST_ORG)
    assert call["actor"] == Actor(id="U_ALICE")
    assert call["principal"].id != "U_ALICE"


def test_dispatcher_binds_org_user_paths_for_the_turn(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def handler(text: str, session: object, sink: object, logger: object) -> None:
        _ = text, session, sink, logger
        scope = current_scope()
        assert scope is not None
        seen["actor_id"] = scope.actor.id
        seen["opensre_home"] = paths.opensre_home()
        seen["session_home"] = paths.session_home()
        seen["integrations"] = paths.integrations_store_path()

    _dispatcher(resolver=_RecordingResolver(), handler=handler).dispatch(
        _inbound(user_id="U_ALICE")
    )

    assert seen["actor_id"] == "U_ALICE"
    assert seen["opensre_home"] == tmp_path / "orgs" / TEST_ORG
    assert seen["session_home"] == tmp_path / "orgs" / TEST_ORG / "users" / "U_ALICE"
    assert seen["integrations"] == tmp_path / "orgs" / TEST_ORG / "integrations.json"
    # Scope must not leak after the turn.
    assert current_scope() is None


def test_alice_and_bob_turns_bind_distinct_session_homes(tmp_path: Path) -> None:
    homes: list[Path] = []

    def handler(text: str, session: object, sink: object, logger: object) -> None:
        _ = text, session, sink, logger
        homes.append(paths.session_home())

    dispatcher = _dispatcher(resolver=_RecordingResolver(), handler=handler)
    dispatcher.dispatch(_inbound(user_id="U_ALICE", text="alice ask"))
    dispatcher.dispatch(_inbound(user_id="U_BOB", text="bob ask"))

    assert homes == [
        tmp_path / "orgs" / TEST_ORG / "users" / "U_ALICE",
        tmp_path / "orgs" / TEST_ORG / "users" / "U_BOB",
    ]
    assert homes[0] != homes[1]


def test_consume_credits_uses_org_principal_not_slack_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    billed: list[str] = []

    def _consume(organization_id: str, *args: object, **kwargs: object) -> CreditsOutcome:
        _ = args, kwargs
        billed.append(organization_id)
        return CreditsOutcome.ALLOWED

    monkeypatch.setattr(turn_metering, "consume_credits", _consume)
    _dispatcher(
        resolver=_RecordingResolver(),
        handler=metered_callback(lambda *_a: None),
    ).dispatch(_inbound(user_id="U_ALICE"))
    assert billed == [TEST_ORG]


def test_unresolved_principal_never_binds_scope_or_runs_handler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)
    ran = False

    def handler(*_a: object) -> None:
        nonlocal ran
        ran = True

    _dispatcher(resolver=_RecordingResolver(), handler=handler).dispatch(_inbound())
    assert ran is False
    assert current_scope() is None
    # No org directory created for a refused turn.
    assert not (tmp_path / "orgs").exists()


def test_slack_scope_helper_stays_in_slack_package() -> None:
    scope = slack_scope(Principal.org("org_x"), "U9")
    assert scope.principal == Principal.org("org_x")
    assert scope.actor == Actor(id="U9")

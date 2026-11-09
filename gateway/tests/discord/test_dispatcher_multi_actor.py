"""Discord dispatcher: Alice/Bob in one thread get distinct sessions."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from config.constants import paths
from config.constants.billing import ORGANIZATION_ID_ENV, USAGE_SECRET_ENV, WEBAPP_URL_ENV
from config.principal import Principal
from core.agent_harness.session import InMemorySessionStore, SessionCore, SessionManager
from gateway.core.storage import FileBindingStore, SessionResolver
from gateway.transports.discord.dispatcher import DiscordTurnDispatcher
from gateway.transports.discord.events import DiscordInboundMessage
from gateway.transports.discord.security import DiscordInboundDecision
from gateway.transports.discord.settings import DiscordGatewaySettings

_ORG = "org_discord_concurrency"
_GUILD = "G_SHARED"
_CHANNEL = "C_SHARED"
_THREAD = "T_SHARED"
ALICE = "U_ALICE"
BOB = "U_BOB"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    monkeypatch.setenv(ORGANIZATION_ID_ENV, _ORG)
    for name in (WEBAPP_URL_ENV, USAGE_SECRET_ENV):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


@pytest.fixture
def resolver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionResolver:
    monkeypatch.setattr(SessionCore, "warm_resolved_integrations", lambda _self, **_k: None)
    monkeypatch.setattr(SessionCore, "hydrate_configured_integrations", lambda _self: None)
    store = FileBindingStore(tmp_path / "bindings.json")
    repo = SimpleNamespace(load_session=lambda _session_id: None)
    manager = SessionManager(store=InMemorySessionStore(), repo=repo)
    return SessionResolver(store, manager=manager, platform="discord")


def _settings() -> DiscordGatewaySettings:
    return DiscordGatewaySettings(
        bot_token="discord-test-token",
        allowed_user_ids=[ALICE, BOB],
        allow_open_guild=False,
        status_update_interval_seconds=0.01,
        turn_timeout_seconds=60.0,
    )


def _inbound(*, user_id: str, text: str = "hello") -> DiscordInboundMessage:
    return DiscordInboundMessage(
        guild_id=_GUILD,
        user_id=user_id,
        channel_id=_CHANNEL,
        message_id=f"m-{user_id}",
        thread_id=_THREAD,
        text=text,
        addressed=True,
    )


def _join(threads: list[threading.Thread], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    for thread in threads:
        remaining = max(0.1, deadline - time.monotonic())
        thread.join(timeout=remaining)
        assert not thread.is_alive(), f"thread {thread.name} did not finish"


def test_alice_and_bob_parallel_turns_get_distinct_sessions(
    resolver: SessionResolver,
) -> None:
    sessions: dict[str, str] = {}
    lock = threading.Lock()

    def handler(text: str, session: SessionCore, sink: Any, logger: logging.Logger) -> None:
        _ = (text, sink, logger)
        with lock:
            sessions[session.session_id] = session.session_id

    dispatcher = DiscordTurnDispatcher(
        settings=_settings(),
        bot_token="discord-test-token",
        session_resolver=resolver,
        handler=handler,
        logger=logging.getLogger("test-discord-dispatcher"),
        bot_user_id="BOT",
    )

    allow = DiscordInboundDecision(allowed=True)
    with (
        patch(
            "gateway.transports.discord.dispatcher.enforce_inbound_discord_message_security",
            return_value=allow,
        ),
        patch("gateway.transports.discord.dispatcher.add_reaction", return_value=True),
        patch("gateway.transports.discord.dispatcher.remove_reaction", return_value=True),
        patch("gateway.transports.discord.turn_output.send_message", return_value="msg-status"),
        patch("gateway.transports.discord.turn_output.edit_message", return_value=True),
        patch(
            "gateway.transports.discord.turn_output.edit_message_with_components", return_value=True
        ),
        patch(
            "gateway.transports.discord.turn_output.send_message_with_components",
            return_value="msg-final",
        ),
    ):
        threads = [
            threading.Thread(
                target=dispatcher.dispatch,
                args=(_inbound(user_id=ALICE, text="alice-turn"),),
                name="alice",
            ),
            threading.Thread(
                target=dispatcher.dispatch,
                args=(_inbound(user_id=BOB, text="bob-turn"),),
                name="bob",
            ),
        ]
        for thread in threads:
            thread.start()
        _join(threads)

    assert len(sessions) == 2

    conversation = f"{_GUILD}:{_CHANNEL}:{_THREAD}"
    principal = Principal.org(_ORG)
    alice_session = resolver.resolve(
        user_id=conversation,
        chat_id=_CHANNEL,
        principal=principal,
        actor=ALICE,
    )
    bob_session = resolver.resolve(
        user_id=conversation,
        chat_id=_CHANNEL,
        principal=principal,
        actor=BOB,
    )
    assert alice_session.session_id != bob_session.session_id

"""Dispatcher-level concurrency: many teammates, distinct sessions, no binding loss.

Complements ``gateway/tests/test_multi_actor_concurrency.py`` (store layer) by
driving ``SlackTurnDispatcher._run_turn`` the way the Socket Mode pool does —
several threads, real ``SessionResolver`` + ``FileBindingStore``, mocked LLM
handler.
"""

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
from gateway.transports.slack.processing.dispatcher import SlackTurnDispatcher
from gateway.transports.slack.processing.events import SlackInboundMessage
from gateway.transports.slack.processing.principal import slack_scope
from gateway.transports.slack.processing.security import SlackInboundDecision
from gateway.transports.slack.settings import SlackGatewaySettings

_ORG = "org_dispatcher_concurrency"
_TEAM = "T_CONC"
_CHANNEL = "C_SHARED"
ALICE = "U_ALICE"
BOB = "U_BOB"


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
    return SessionResolver(store, manager=manager, platform="slack")


def _settings() -> SlackGatewaySettings:
    return SlackGatewaySettings(
        bot_token="xoxb-test",
        app_token="xapp-test",
        allowed_user_ids=[],
        allow_open_workspace=True,
        status_update_interval_seconds=0.01,
        turn_timeout_seconds=60.0,
    )


def _inbound(*, user_id: str, thread_ts: str, text: str = "hello") -> SlackInboundMessage:
    return SlackInboundMessage(
        team_id=_TEAM,
        user_id=user_id,
        channel_id=_CHANNEL,
        ts=thread_ts,
        thread_ts=thread_ts,
        text=text,
    )


def _join(threads: list[threading.Thread], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    alive = [t.name for t in threads if t.is_alive()]
    assert not alive, f"threads still running: {alive}"


def _run_turn(
    dispatcher: SlackTurnDispatcher,
    *,
    user_id: str,
    thread_ts: str,
    text: str = "hello",
) -> None:
    inbound = _inbound(user_id=user_id, thread_ts=thread_ts, text=text)
    scope = slack_scope(Principal.org(_ORG), user_id)
    dispatcher._run_turn(inbound, scope)


@pytest.fixture
def allow_all_security():
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


@pytest.mark.usefixtures("allow_all_security")
def test_concurrent_alice_bob_different_threads_keep_distinct_sessions(
    resolver: SessionResolver,
) -> None:
    """Parallel pool turns (different Slack threads) must not share sessions."""
    turns: list[tuple[str, str]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def handler(text: str, session: Any, sink: Any, _logger: logging.Logger) -> None:
        barrier.wait(timeout=5)
        with lock:
            # user id is embedded in the agent text prefix.
            turns.append((text, session.session_id))
        sink.finalize("ok")

    dispatcher = SlackTurnDispatcher(
        settings=_settings(),
        messaging=_FakeMessagingClient(),
        session_resolver=resolver,
        handler=handler,
        logger=logging.getLogger("test.conc"),
    )

    threads = [
        threading.Thread(
            target=_run_turn,
            kwargs={"dispatcher": dispatcher, "user_id": ALICE, "thread_ts": "100.1"},
            name="alice",
        ),
        threading.Thread(
            target=_run_turn,
            kwargs={"dispatcher": dispatcher, "user_id": BOB, "thread_ts": "200.1"},
            name="bob",
        ),
    ]
    for t in threads:
        t.start()
    _join(threads)

    assert len(turns) == 2
    by_user = {}
    for text, session_id in turns:
        if f"user=<@{ALICE}>" in text:
            by_user[ALICE] = session_id
        elif f"user=<@{BOB}>" in text:
            by_user[BOB] = session_id
    assert set(by_user) == {ALICE, BOB}
    assert by_user[ALICE] != by_user[BOB]

    alice_bound = resolver._bindings.get_session_id(
        platform="slack",
        chat_id=f"{_TEAM}:{_CHANNEL}:100.1",
        principal=Principal.org(_ORG),
        actor=ALICE,
    )
    bob_bound = resolver._bindings.get_session_id(
        platform="slack",
        chat_id=f"{_TEAM}:{_CHANNEL}:200.1",
        principal=Principal.org(_ORG),
        actor=BOB,
    )
    assert alice_bound == by_user[ALICE]
    assert bob_bound == by_user[BOB]
    # Cross-actor lookups on the other conversation must miss.
    assert (
        resolver._bindings.get_session_id(
            platform="slack",
            chat_id=f"{_TEAM}:{_CHANNEL}:100.1",
            principal=Principal.org(_ORG),
            actor=BOB,
        )
        is None
    )


@pytest.mark.usefixtures("allow_all_security")
def test_same_thread_alice_bob_interleaved_turns_stay_isolated(
    resolver: SessionResolver,
) -> None:
    """Same Slack thread: conversation lock serializes, sessions stay per actor."""
    thread_ts = "300.1"
    conversation_key = f"{_TEAM}:{_CHANNEL}:{thread_ts}"
    seen: list[tuple[str, str]] = []
    lock = threading.Lock()

    def handler(text: str, session: Any, sink: Any, _logger: logging.Logger) -> None:
        with lock:
            if f"user=<@{ALICE}>" in text:
                seen.append((ALICE, session.session_id))
            elif f"user=<@{BOB}>" in text:
                seen.append((BOB, session.session_id))
        # Brief pause so the other thread contends on the conversation lock.
        time.sleep(0.02)
        sink.finalize("ok")

    dispatcher = SlackTurnDispatcher(
        settings=_settings(),
        messaging=_FakeMessagingClient(),
        session_resolver=resolver,
        handler=handler,
        logger=logging.getLogger("test.same-thread"),
    )

    order = [ALICE, BOB, ALICE, BOB, ALICE, BOB]
    barrier = threading.Barrier(len(order))
    errors: list[Exception] = []

    def worker(user_id: str, idx: int) -> None:
        try:
            barrier.wait(timeout=5)
            _run_turn(
                dispatcher,
                user_id=user_id,
                thread_ts=thread_ts,
                text=f"turn-{idx}",
            )
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(user_id, i), name=f"{user_id}-{i}")
        for i, user_id in enumerate(order)
    ]
    for t in threads:
        t.start()
    _join(threads)

    assert not errors, errors
    assert len(seen) == 6
    alice_ids = {sid for user, sid in seen if user == ALICE}
    bob_ids = {sid for user, sid in seen if user == BOB}
    assert len(alice_ids) == 1
    assert len(bob_ids) == 1
    assert alice_ids.isdisjoint(bob_ids)

    assert resolver._bindings.get_session_id(
        platform="slack",
        chat_id=conversation_key,
        principal=Principal.org(_ORG),
        actor=ALICE,
    ) == next(iter(alice_ids))
    assert resolver._bindings.get_session_id(
        platform="slack",
        chat_id=conversation_key,
        principal=Principal.org(_ORG),
        actor=BOB,
    ) == next(iter(bob_ids))


@pytest.mark.usefixtures("allow_all_security")
def test_many_actors_parallel_turns_all_bindings_survive(
    resolver: SessionResolver,
) -> None:
    """Fan-out across N actors / N threads — every binding row remains."""
    n = 8
    barrier = threading.Barrier(n)
    errors: list[Exception] = []
    sessions: dict[str, str] = {}
    lock = threading.Lock()

    def handler(text: str, session: Any, sink: Any, _logger: logging.Logger) -> None:
        barrier.wait(timeout=5)
        for i in range(n):
            uid = f"U_{i:03d}"
            if f"user=<@{uid}>" in text:
                with lock:
                    sessions[uid] = session.session_id
                break
        sink.finalize("ok")

    dispatcher = SlackTurnDispatcher(
        settings=_settings(),
        messaging=_FakeMessagingClient(),
        session_resolver=resolver,
        handler=handler,
        logger=logging.getLogger("test.fanout"),
    )

    def worker(idx: int) -> None:
        try:
            _run_turn(
                dispatcher,
                user_id=f"U_{idx:03d}",
                thread_ts=f"{1000 + idx}.1",
                text=f"hi-{idx}",
            )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,), name=f"u{i}") for i in range(n)]
    for t in threads:
        t.start()
    _join(threads)

    assert not errors, errors
    assert set(sessions) == {f"U_{i:03d}" for i in range(n)}
    assert len(set(sessions.values())) == n

    for i in range(n):
        uid = f"U_{i:03d}"
        bound = resolver._bindings.get_session_id(
            platform="slack",
            chat_id=f"{_TEAM}:{_CHANNEL}:{1000 + i}.1",
            principal=Principal.org(_ORG),
            actor=uid,
        )
        assert bound == sessions[uid]

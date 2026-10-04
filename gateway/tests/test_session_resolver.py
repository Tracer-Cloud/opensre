from __future__ import annotations

from types import SimpleNamespace

import pytest

from config.constants.conversation_history import OPENSRE_STRUCTURED_HISTORY_ENV
from config.principal import Principal
from core.agent_harness.prompts import build_action_system_prompt
from core.agent_harness.session import InMemorySessionStore, SessionCore, SessionManager
from core.agent_harness.turns.structured_history import history_messages
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from gateway.core.storage import FileBindingStore, SessionResolver


@pytest.fixture
def principal() -> Principal:
    return Principal.individual("tg-user-42")


@pytest.fixture
def resolver(tmp_path, monkeypatch) -> SessionResolver:
    # Keep integration bootstrap a no-op so tests don't resolve real integrations.
    monkeypatch.setattr(SessionCore, "warm_resolved_integrations", lambda _self, **_k: None)
    monkeypatch.setattr(SessionCore, "hydrate_configured_integrations", lambda _self: None)

    store = FileBindingStore(tmp_path / "bindings.json")
    # A mutable fake repo whose load_session each test can override.
    repo = SimpleNamespace(load_session=lambda _session_id: None)
    manager = SessionManager(store=InMemorySessionStore(), repo=repo)
    resolver = SessionResolver(store, manager=manager)
    resolver._fake_repo = repo  # test handle to swap load_session
    return resolver


def test_resolve_creates_and_injects_gateway_chat_context(resolver: SessionResolver) -> None:
    resolved = resolver.resolve(
        user_id="42", chat_id="99", principal=Principal.individual("tg-user-42"), actor=None
    )

    # New session was created, bound, and tagged with the per-turn chat id.
    assert resolved.resolved_integrations_cache["_gateway_chat_id"] == "99"
    assert (
        resolver._bindings.get_session_id(
            platform="telegram", chat_id="42", principal=Principal.individual("tg-user-42")
        )
        == resolved.session_id
    )


def test_resolve_restores_persisted_conversation_context(resolver: SessionResolver) -> None:
    resolver._bindings.bind(
        platform="telegram",
        chat_id="42",
        session_id="session-1",
        principal=Principal.individual("tg-user-42"),
    )
    resolver._fake_repo.load_session = lambda session_id: {
        "session_id": session_id,
        "cli_agent_messages": [
            ("user", "weather in Hawaii"),
            ("assistant", "Hawaii: +28C"),
            ("user", "send that to Slack"),
            (
                "assistant",
                'slack_send_message input: {"message": "Hawaii: +28C"}\n'
                'slack_send_message result: {"status": "sent"}',
            ),
        ],
        "accumulated_context": {"service": "checkout"},
        "history": [{"type": "shell", "text": "curl wttr.in/Hawaii", "ok": True}],
    }

    resolved = resolver.resolve(
        user_id="42", chat_id="99", principal=Principal.individual("tg-user-42"), actor=None
    )

    assert resolved.cli_agent_messages[-1] == (
        "assistant",
        'slack_send_message input: {"message": "Hawaii: +28C"}\n'
        'slack_send_message result: {"status": "sent"}',
    )
    assert resolved.accumulated_context == {"service": "checkout"}
    assert resolved.history == [{"type": "shell", "text": "curl wttr.in/Hawaii", "ok": True}]
    assert resolved.resolved_integrations_cache["_gateway_chat_id"] == "99"


def test_resolved_telegram_context_is_visible_as_prior_action_facts(
    resolver: SessionResolver, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(OPENSRE_STRUCTURED_HISTORY_ENV, "0")
    resolver._bindings.bind(
        platform="telegram",
        chat_id="42",
        session_id="session-1",
        principal=Principal.individual("tg-user-42"),
    )
    resolver._fake_repo.load_session = lambda session_id: {
        "session_id": session_id,
        "cli_agent_messages": [
            ("user", "Can you send the weather of both hawaii and antartica to slack?"),
            (
                "assistant",
                "Hawaii: +28C\n"
                "Antarctica: -24C\n"
                'slack_send_message input: {"message": "Hawaii: +28C\\nAntarctica: -24C"}\n'
                'slack_send_message result: {"sent": true}',
            ),
            ("user", "Write it in a nicer message and compare to London"),
            ("assistant", "London: +22C"),
        ],
    }

    resolved = resolver.resolve(
        user_id="42", chat_id="99", principal=Principal.individual("tg-user-42"), actor=None
    )

    prompt = build_action_system_prompt(
        TurnSnapshot.from_session(
            "No, compute those temperatures and send the nice comparison to Slack",
            resolved,
            surface="interactive_shell",
        )
    )

    assert "PRIOR ACTION FACTS" in prompt
    assert "Hawaii: +28C" in prompt
    assert "Antarctica: -24C" in prompt
    assert "London: +22C" in prompt
    assert "slack_send_message input" in prompt


def test_resolved_telegram_context_reaches_the_model_as_earlier_turns(
    resolver: SessionResolver,
) -> None:
    resolver._bindings.bind(
        platform="telegram",
        chat_id="42",
        session_id="session-1",
        principal=Principal.individual("tg-user-42"),
    )
    resolver._fake_repo.load_session = lambda session_id: {
        "session_id": session_id,
        "cli_agent_messages": [
            ("user", "Send the weather of hawaii and antarctica to slack"),
            ("assistant", "Hawaii: +28C\nAntarctica: -24C"),
        ],
    }

    resolved = resolver.resolve(
        user_id="42", chat_id="99", principal=Principal.individual("tg-user-42"), actor=None
    )
    snapshot = TurnSnapshot.from_session("Compare them", resolved, surface="interactive_shell")
    history = history_messages(snapshot.conversation_messages, snapshot.turn_evidence)

    assert [(message.role, message.content) for message in history] == [
        ("user", "Send the weather of hawaii and antarctica to slack"),
        ("assistant", "Hawaii: +28C\nAntarctica: -24C"),
    ]
    assert "RECENT CONVERSATION" not in build_action_system_prompt(snapshot)


def test_rotate_flushes_old_and_binds_new(resolver: SessionResolver) -> None:
    first = resolver.resolve(
        user_id="42", chat_id="99", principal=Principal.individual("tg-user-42"), actor=None
    )
    rotated = resolver.rotate(
        user_id="42", chat_id="99", principal=Principal.individual("tg-user-42"), actor=None
    )

    assert rotated.session_id != first.session_id
    assert rotated.resolved_integrations_cache["_gateway_chat_id"] == "99"
    assert (
        resolver._bindings.get_session_id(
            platform="telegram", chat_id="42", principal=Principal.individual("tg-user-42")
        )
        == rotated.session_id
    )


def test_resolve_adopts_legacy_empty_binding_into_scoped_key(
    resolver: SessionResolver,
) -> None:
    """Same-document legacy Telegram rows are re-keyed on first scoped resolve."""
    org = Principal.org("org_acme")
    resolver._bindings.bind(
        platform="telegram",
        chat_id="42",
        session_id="legacy-session",
    )
    resolver._fake_repo.load_session = lambda session_id: {
        "session_id": session_id,
        "cli_agent_messages": [],
    }

    resolved = resolver.resolve(user_id="42", chat_id="99", principal=org, actor="tg-42")

    assert resolved.session_id == "legacy-session"
    assert (
        resolver._bindings.get_session_id(
            platform="telegram", chat_id="42", principal=org, actor="tg-42"
        )
        == "legacy-session"
    )
    # Single-use: legacy empty-id row must be gone after adoption.
    assert resolver._bindings.get_session_id(platform="telegram", chat_id="42") is None


def test_legacy_adoption_is_single_use_across_actors(resolver: SessionResolver) -> None:
    """Second actor must not inherit the session the first actor adopted."""
    org = Principal.org("org_acme")
    resolver._bindings.bind(
        platform="telegram",
        chat_id="T:C:1",
        session_id="legacy-shared",
    )
    resolver._fake_repo.load_session = lambda session_id: {
        "session_id": session_id,
        "cli_agent_messages": [],
    }

    alice = resolver.resolve(user_id="T:C:1", chat_id="C1", principal=org, actor="U_ALICE")
    bob = resolver.resolve(user_id="T:C:1", chat_id="C1", principal=org, actor="U_BOB")

    assert alice.session_id == "legacy-shared"
    assert bob.session_id != alice.session_id
    assert resolver._bindings.get_session_id(platform="telegram", chat_id="T:C:1") is None


def test_slack_actors_get_distinct_sessions(resolver: SessionResolver) -> None:
    org = Principal.org("org_acme")
    alice = resolver.resolve(user_id="T:C:1", chat_id="C1", principal=org, actor="U_ALICE")
    bob = resolver.resolve(user_id="T:C:1", chat_id="C1", principal=org, actor="U_BOB")

    assert alice.session_id != bob.session_id
    assert resolver.has_conversation(conversation_key="T:C:1", principal=org)
    for actor, session in (("U_ALICE", alice), ("U_BOB", bob)):
        assert (
            resolver._bindings.get_session_id(
                platform="telegram", chat_id="T:C:1", principal=org, actor=actor
            )
            == session.session_id
        )


def test_resolve_and_rotate_require_explicit_principal_and_actor(
    resolver: SessionResolver,
) -> None:
    """Omitting scope must fail loudly, not silently degrade the binding key.

    The parameters defaulted to ``None``, and a transport that forgot them
    (Buzz) shipped with scoped binding silently short-circuited to legacy
    empty-key rows. Legacy rows stay expressible — by passing ``None``
    explicitly — but silence is no longer an option.
    """
    with pytest.raises(TypeError):
        resolver.resolve(user_id="42", chat_id="99")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        resolver.resolve(user_id="42", chat_id="99", principal=Principal.individual("tg-user-42"))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        resolver.rotate(user_id="42", chat_id="99")  # type: ignore[call-arg]

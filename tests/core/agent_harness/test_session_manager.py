"""Tests for the centralized session lifecycle owner."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent_harness.session import (
    InMemorySessionStore,
    SessionCore,
    SessionManager,
)
from surfaces.interactive_shell.session import (
    Session,
)


@pytest.fixture(autouse=True)
def _no_real_integration_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep bootstrap from resolving real integrations during unit tests.
    monkeypatch.setattr(SessionCore, "warm_resolved_integrations", lambda _self, **_k: None)
    monkeypatch.setattr(SessionCore, "hydrate_configured_integrations", lambda _self: None)


def _manager(*, repo=None) -> SessionManager:
    return SessionManager(
        store=InMemorySessionStore(),
        repo=repo or SimpleNamespace(load_session=lambda _sid: None),
    )


def test_open_store_opens_bootstrapped_session() -> None:
    storage = InMemorySessionStore()
    opened: list[str] = []
    storage.open_session = lambda session: opened.append(session.session_id)  # type: ignore[method-assign]
    manager = SessionManager(store=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    session = Session(session_id="boot-only")
    manager.bootstrap(session, hydrate_integrations=False, persistent_tasks=False)
    manager.open_store(session)

    assert session.store is storage
    assert opened == ["boot-only"]


def test_create_opens_storage_and_returns_session() -> None:
    storage = InMemorySessionStore()
    opened: list[str] = []
    storage.open_session = lambda session: opened.append(session.session_id)  # type: ignore[method-assign]
    manager = SessionManager(store=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    session = manager.create()

    assert isinstance(session, SessionCore)
    assert opened == [session.session_id]


def test_create_with_explicit_session_id() -> None:
    session = _manager().create(session_id="fixed-id", open_store=False)
    assert session.session_id == "fixed-id"


def test_resolve_restores_context_and_reopens_storage() -> None:
    storage = InMemorySessionStore()
    reopened: list[str] = []
    storage.reopen_session = lambda session_id: reopened.append(session_id)  # type: ignore[method-assign]
    repo = SimpleNamespace(
        load_session=lambda session_id: {
            "session_id": session_id,
            "cli_agent_messages": [("user", "hi"), ("assistant", "hello")],
            "accumulated_context": {"service": "checkout"},
            "history": [{"type": "shell", "text": "ls", "ok": True}],
        }
    )
    manager = SessionManager(store=storage, repo=repo)

    session = manager.resolve("sess-1")

    assert session.session_id == "sess-1"
    assert session.cli_agent_messages == [("user", "hi"), ("assistant", "hello")]
    assert session.accumulated_context == {"service": "checkout"}
    assert session.history == [{"type": "shell", "text": "ls", "ok": True}]
    assert reopened == ["sess-1"]


def test_resolve_restores_dangling_tool_recovery_note() -> None:
    repo = SimpleNamespace(
        load_session=lambda session_id: {
            "session_id": session_id,
            "dangling_tool_intents": [
                {
                    "tool_call_id": "call-1",
                    "tool": "shell_run",
                    "arguments": {"command": "deploy", "args": ["api"]},
                    "user_text": "Deploy the API",
                }
            ],
        }
    )

    session = SessionManager(store=InMemorySessionStore(), repo=repo).resolve("sess-1")

    assert session.pending_recovery_note is not None
    assert "shell_run deploy api" in session.pending_recovery_note
    assert "never blindly repeat" in session.pending_recovery_note


def test_restore_context_clears_a_recovery_note_once_intents_are_committed() -> None:
    session = Session(session_id="sess-1")
    session.pending_recovery_note = "stale recovery note"

    _manager().restore_context(session, {"dangling_tool_intents": []})

    assert session.pending_recovery_note is None


def test_restore_context_ignores_empty_and_malformed() -> None:
    manager = _manager()
    session = Session(session_id="s")

    assert manager.restore_context(session, None) is session
    assert session.cli_agent_messages == []

    manager.restore_context(
        session,
        {"cli_agent_messages": [("user", "ok"), "bad-entry", ("system", "x"), ("assistant", "")]},
    )
    # Only well-formed user/assistant pairs with content survive.
    assert session.cli_agent_messages == [("user", "ok")]


def test_rotate_closes_old_and_creates_new() -> None:
    storage = InMemorySessionStore()
    flushed: list[str] = []
    storage.flush = lambda session: flushed.append(session.session_id)  # type: ignore[method-assign]
    manager = SessionManager(store=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    session = manager.rotate(old_session_id="old-1", new_session_id="new-1")

    assert flushed == ["old-1"]
    assert session.session_id == "new-1"


def test_rotate_restores_outgoing_transcript_for_memory_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = InMemorySessionStore()
    scheduled: list[tuple[list[tuple[str, str]], bool]] = []

    def _schedule(messages: list[tuple[str, str]], *, wait_for_completion: bool = False) -> None:
        scheduled.append((list(messages), wait_for_completion))

    monkeypatch.setattr(
        "core.agent_harness.session.memory_extraction.schedule_memory_extraction",
        _schedule,
    )
    repo = SimpleNamespace(
        load_session=lambda _sid: {
            "cli_agent_messages": [
                ("user", "prod cluster is eks-prod-1"),
                ("assistant", "got it"),
            ]
        }
    )
    manager = SessionManager(store=storage, repo=repo)

    manager.rotate(old_session_id="old-1", new_session_id="new-1")

    assert len(scheduled) == 1
    assert scheduled[0][0] == [("user", "prod cluster is eks-prod-1"), ("assistant", "got it")]
    assert scheduled[0][1] is False  # rotate must not block on extraction


def test_rotate_without_old_id_skips_close() -> None:
    storage = InMemorySessionStore()
    flushed: list[str] = []
    storage.flush = lambda session: flushed.append(session.session_id)  # type: ignore[method-assign]
    manager = SessionManager(store=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    manager.rotate(new_session_id="new-1")

    assert flushed == []


def test_bootstrap_sets_persistent_task_registry() -> None:
    session = Session(session_id="s")
    before = session.task_registry
    _manager().bootstrap(session)
    assert session.task_registry is not before
    assert session.runtime_metadata.get("opensre_version")


def test_created_session_persists_through_manager_storage() -> None:
    # Regression: the session's own storage backend must be the manager's, so
    # session.record() writes to the same place the manager opens/flushes —
    # not the default JSONL field on Session.
    storage = InMemorySessionStore()
    manager = SessionManager(store=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    session = manager.create()
    assert session.store is storage

    session.record("chat", "hello")
    assert any("hello" in str(rec) for rec in storage.read(session.session_id))


def test_close_persists_and_releases_resources() -> None:
    storage = InMemorySessionStore()
    flushed: list[str] = []
    storage.flush = lambda session: flushed.append(session.session_id)  # type: ignore[method-assign]
    manager = SessionManager(store=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    session = Session(session_id="s-close")
    session.store = storage
    session.terminal.prompt_refresh_fn = lambda: None

    manager.close(session)

    assert flushed == ["s-close"]
    assert session.terminal.prompt_refresh_fn is None


def test_close_releases_resources_before_memory_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = InMemorySessionStore()
    manager = SessionManager(store=storage, repo=SimpleNamespace(load_session=lambda _sid: None))
    session = Session(session_id="s-close-order")
    session.store = storage
    session.agent.messages = [("user", "remember eks-prod-1"), ("assistant", "saved")]
    session.terminal.prompt_refresh_fn = lambda: None

    events: list[str] = []

    def _release() -> None:
        events.append("release")
        session.terminal.prompt_refresh_fn = None

    def _schedule(messages: list[tuple[str, str]], *, wait_for_completion: bool = False) -> None:
        events.append(f"extract:{len(messages)}:{wait_for_completion}")

    monkeypatch.setattr(session, "release_resources", _release)
    monkeypatch.setattr(
        "core.agent_harness.session.memory_extraction.schedule_memory_extraction",
        _schedule,
    )

    manager.close(session)

    assert events == ["release", "extract:2:True"]
    assert session.terminal.prompt_refresh_fn is None


def test_close_flush_failure_does_not_crash_teardown() -> None:
    storage = InMemorySessionStore()

    def _boom(_session: object) -> None:
        raise OSError("disk full")

    storage.flush = _boom  # type: ignore[method-assign]
    manager = SessionManager(store=storage, repo=SimpleNamespace(load_session=lambda _sid: None))
    session = Session(session_id="s-fail")
    session.store = storage
    session.terminal.prompt_refresh_fn = lambda: None

    # Must not raise; resources still released.
    manager.close(session)
    assert session.terminal.prompt_refresh_fn is None


def test_rotate_in_place_flushes_clears_and_opens_new_id() -> None:
    storage = InMemorySessionStore()
    flushed: list[str] = []
    opened: list[str] = []
    storage.flush = lambda session: flushed.append(session.session_id)  # type: ignore[method-assign]
    storage.open_session = lambda session: opened.append(session.session_id)  # type: ignore[method-assign]
    manager = SessionManager(store=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    session = Session(session_id="old-id")
    session.store = storage
    session.agent.messages = [("user", "carry")]
    session.accumulated_context["svc"] = "checkout"

    refresh = lambda: None  # noqa: E731 — loop-owned prompt hook stand-in
    session.terminal.prompt_refresh_fn = refresh

    manager.rotate_in_place(session)

    assert flushed == ["old-id"]
    assert session.session_id != "old-id"
    assert opened == [session.session_id]
    assert session.agent.messages == []
    assert session.accumulated_context == {}
    # Regression: in-place reuse must NOT drop the loop-owned prompt hook.
    assert session.terminal.prompt_refresh_fn is refresh


def test_rotate_in_place_schedules_extraction_before_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = InMemorySessionStore()
    manager = SessionManager(store=storage, repo=SimpleNamespace(load_session=lambda _sid: None))
    session = Session(session_id="old-id")
    session.store = storage
    session.agent.messages = [("user", "my name is Ada"), ("assistant", "noted")]

    scheduled: list[tuple[list[tuple[str, str]], bool]] = []

    def _schedule(messages: list[tuple[str, str]], *, wait_for_completion: bool = False) -> None:
        scheduled.append((list(messages), wait_for_completion))

    monkeypatch.setattr(
        "core.agent_harness.session.memory_extraction.schedule_memory_extraction",
        _schedule,
    )

    manager.rotate_in_place(session)

    assert scheduled == [
        ([("user", "my name is Ada"), ("assistant", "noted")], False),
    ]
    assert session.agent.messages == []


def test_rebind_for_resume_switches_id_and_reopens_storage() -> None:
    storage = InMemorySessionStore()
    flushed: list[str] = []
    reopened: list[str] = []
    storage.flush = lambda session: flushed.append(session.session_id)  # type: ignore[method-assign]
    storage.reopen_session = lambda session_id: reopened.append(session_id)  # type: ignore[method-assign]
    manager = SessionManager(store=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    session = Session(session_id="live-id")
    session.store = storage
    refresh = lambda: None  # noqa: E731 — loop-owned prompt hook stand-in
    session.terminal.prompt_refresh_fn = refresh

    manager.rebind_for_resume(session, session_id="saved-id", started_at="2026-01-15T10:00:00")

    assert flushed == ["live-id"]
    assert session.session_id == "saved-id"
    assert reopened == ["saved-id"]
    # Regression: /resume reuses the live handle — keep the prompt hook.
    assert session.terminal.prompt_refresh_fn is refresh


def test_rebind_for_resume_same_id_clears_without_flush() -> None:
    storage = InMemorySessionStore()
    flushed: list[str] = []
    storage.flush = lambda session: flushed.append(session.session_id)  # type: ignore[method-assign]
    manager = SessionManager(store=storage, repo=SimpleNamespace(load_session=lambda _sid: None))

    session = Session(session_id="same-id")
    session.store = storage
    session.history = [{"type": "shell", "text": "ls", "ok": True}]

    manager.rebind_for_resume(session, session_id="same-id")

    assert flushed == []
    assert session.session_id == "same-id"
    assert session.history == []


def test_closed_session_is_garbage_collectable() -> None:
    # Memory-leak guard: after close(), dropping the last strong reference must
    # let the session be collected — no lingering references (warm task,
    # background closures, prompt hook) keep it alive.
    import gc
    import weakref

    manager = _manager()
    session = Session(session_id="s-gc")
    session.store = InMemorySessionStore()
    session.terminal.prompt_refresh_fn = lambda: None
    ref = weakref.ref(session)

    manager.close(session)
    del session
    gc.collect()

    assert ref() is None, "closed session was not garbage-collected — reference leak"


def test_close_cancels_in_flight_warm_task() -> None:
    manager = _manager()
    session = manager.create(session_id="s-warm")

    class _FakeTask:
        def __init__(self) -> None:
            self.cancelled = False

        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            self.cancelled = True

    task = _FakeTask()
    session.integrations._warm_task = task

    manager.close(session)

    assert task.cancelled is True
    assert session.integrations._warm_task is None

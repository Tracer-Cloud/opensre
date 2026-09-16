"""Regression coverage for resumable-session process boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import surfaces.interactive_shell.main as main_entrypoint
from core.agent_harness.session import InMemorySessionStore, SessionCore, SessionManager
from core.agent_harness.session.pending_choice import parse_ask_user_answers
from infrastructure.turn_host.session_lock import (
    SessionExecutionBusyError,
    retained_session_execution_locks,
    session_execution_lock,
)
from surfaces.interactive_shell.session import Session


def test_legacy_answer_keeps_blank_paragraphs_inside_one_answer() -> None:
    text = "1. Which repository?\nFirst paragraph\n\nSecond paragraph"

    assert parse_ask_user_answers(text) == [
        ("Which repository?", "First paragraph\n\nSecond paragraph")
    ]


def test_rotate_in_place_retains_the_fresh_session_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """No other host can bind the new id before the current turn flushes it."""
    monkeypatch.setattr(
        "infrastructure.turn_host.session_lock.sessions_dir",
        lambda: tmp_path,
    )
    session = SessionCore(store=InMemorySessionStore())
    manager = SessionManager.for_session(session)

    with retained_session_execution_locks():
        manager.rotate_in_place(session)
        with (
            pytest.raises(SessionExecutionBusyError),
            session_execution_lock(session.session_id, timeout=0),
        ):
            pass

    with session_execution_lock(session.session_id, timeout=0):
        pass


def test_repl_shutdown_refreshes_before_closing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idle-shell teardown reconciles state while the shared execution lease is held."""
    events: list[str] = []
    session = Session(session_id="session-123")

    class _Manager:
        def open_store(self, _session: Session) -> None:
            return

        def refresh_from_storage(self, _session: Session) -> None:
            events.append("refresh")

        def close(self, _session: Session) -> None:
            events.append("close")

    @contextmanager
    def _lease(_session_id: str) -> Iterator[None]:
        events.append("lock")
        yield

    class _Controller:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return

        async def start_interactive_shell(self) -> None:
            return

    monkeypatch.setattr(main_entrypoint, "identify_saved_github_username", lambda: None)
    monkeypatch.setattr(main_entrypoint, "offer_demo", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        main_entrypoint,
        "create_repl_runtime",
        lambda **_kwargs: SimpleNamespace(session=session, inbox=None),
    )
    monkeypatch.setattr(main_entrypoint, "InteractiveShellController", _Controller)
    monkeypatch.setattr(main_entrypoint.SessionManager, "for_session", lambda _session: _Manager())
    monkeypatch.setattr(main_entrypoint, "session_execution_lock", _lease)

    assert asyncio.run(main_entrypoint.run_repl_async()) == 0
    assert events == ["lock", "refresh", "close"]

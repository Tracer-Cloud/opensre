"""Session-scoped agent pool and bindable output reuse across turns."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from core.agent_harness.session import SessionCore
from core.agent_harness.session.pending_choice import PendingUserChoice
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from infrastructure.turn_host.bindable_output import BindableOutput
from infrastructure.turn_host.session_agents import SessionAgentPool
from infrastructure.turn_host.session_lock import (
    retain_session_execution_lock,
    retained_session_execution_locks,
    session_execution_lock,
)
from infrastructure.turn_host.turn_runner import TurnRunner
from tests.shared.default_headless_build_stub import default_headless_build_stub
from tests.shared.fake_agent import fake_agent


@pytest.fixture(autouse=True)
def _stub_gateway_turn_analytics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "infrastructure.turn_host.turn_runner.capture_gateway_turn_started", lambda **_: None
    )
    monkeypatch.setattr(
        "infrastructure.turn_host.turn_runner.capture_gateway_turn_completed", lambda **_: None
    )
    monkeypatch.setattr(
        "infrastructure.turn_host.turn_runner.capture_gateway_turn_failed", lambda **_: None
    )


def _empty_result() -> TurnResult:
    return TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
            response_text="",
        ),
        assistant_response_text="",
    )


def test_bindable_output_requires_bind_before_use() -> None:
    bindable = BindableOutput()
    with pytest.raises(RuntimeError, match="not bound"):
        bindable.finalize("x")


def test_bindable_output_rebinds_across_turns() -> None:
    bindable = BindableOutput()
    first = MagicMock()
    second = MagicMock()
    bindable.bind(first)
    bindable.finalize("a")
    first.finalize.assert_called_once_with("a")
    bindable.bind(second)
    bindable.set_tool_status("running")
    second.set_tool_status.assert_called_once_with("running")


def test_pool_reuses_agent_for_same_session(monkeypatch: pytest.MonkeyPatch) -> None:
    constructed: list[Any] = []

    class _FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            constructed.append(kwargs)
            self.bind_turn = MagicMock()
            self.bind_session = MagicMock()
            self.dispatch = MagicMock(return_value=_empty_result())

    def _fake_build(**kwargs: Any) -> Any:
        agent = _FakeAgent(**kwargs)
        return agent

    monkeypatch.setattr(
        "infrastructure.turn_host.session_agents.DefaultHeadlessBuild",
        default_headless_build_stub(_fake_build),
    )
    pool = SessionAgentPool(console=Console(force_terminal=False))
    session = SessionCore(store=InMemorySessionStore())
    logger = logging.getLogger("test.pool")
    first = pool.agent_for(session=session, output=MagicMock(), logger=logger)
    second = pool.agent_for(session=session, output=MagicMock(), logger=logger)
    assert first is second
    assert len(constructed) == 1
    assert session.session_id in pool.cached_session_ids


def test_pool_builds_separate_agents_per_session(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeAgent:
        def __init__(self, **_kwargs: Any) -> None:
            self.bind_turn = MagicMock()
            self.bind_session = MagicMock()

    monkeypatch.setattr(
        "infrastructure.turn_host.session_agents.DefaultHeadlessBuild",
        default_headless_build_stub(lambda **kwargs: _FakeAgent(**kwargs)),
    )
    pool = SessionAgentPool(console=Console(force_terminal=False))
    a = SessionCore(store=InMemorySessionStore())
    b = SessionCore(store=InMemorySessionStore())
    logger = logging.getLogger("test.pool")
    agent_a = pool.agent_for(session=a, output=MagicMock(), logger=logger)
    agent_b = pool.agent_for(session=b, output=MagicMock(), logger=logger)
    assert agent_a is not agent_b
    assert pool.cached_session_ids == frozenset({a.session_id, b.session_id})


def test_pool_rebinds_current_session_on_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gateway resolve() yields a new SessionCore each turn; reuse must follow it."""
    bound: list[Any] = []

    class _FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            self.session = kwargs["session"]
            self.bind_turn = MagicMock()

        def bind_session(self, session: Any) -> None:
            bound.append(session)
            self.session = session

    monkeypatch.setattr(
        "infrastructure.turn_host.session_agents.DefaultHeadlessBuild",
        default_headless_build_stub(lambda **kwargs: _FakeAgent(**kwargs)),
    )
    pool = SessionAgentPool(console=Console(force_terminal=False))
    first = SessionCore(store=InMemorySessionStore())
    # Same logical id, different object — what SessionManager.resolve returns.
    second = SessionCore(store=InMemorySessionStore(), session_id=first.session_id)
    logger = logging.getLogger("test.pool.rebind")
    agent_a = pool.agent_for(session=first, output=MagicMock(), logger=logger)
    agent_b = pool.agent_for(session=second, output=MagicMock(), logger=logger)
    assert agent_a is agent_b
    assert bound == [second]
    assert agent_b.session is second


def test_turn_runner_reuses_headless_agent_across_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = fake_agent(dispatch_result=_empty_result())
    factory = MagicMock(return_value=agent)
    monkeypatch.setattr(
        "infrastructure.turn_host.session_agents.DefaultHeadlessBuild",
        default_headless_build_stub(factory),
    )

    session = SessionCore(store=InMemorySessionStore())
    handler = TurnRunner(console=Console(force_terminal=False))
    logger = logging.getLogger("test.reuse")
    handler("one", session, MagicMock(), logger)
    handler("two", session, MagicMock(), logger)

    assert factory.call_count == 1
    assert agent.dispatch.call_count == 2
    assert agent.bind_turn.call_count == 2


def _fake_agent_pool(monkeypatch: pytest.MonkeyPatch) -> SessionAgentPool:
    """Pool whose agents are stubs — these tests exercise locking, not dispatch."""

    class _FakeAgent:
        def __init__(self, **_kwargs: Any) -> None:
            self.bind_turn = MagicMock()
            self.bind_session = MagicMock()

    monkeypatch.setattr(
        "infrastructure.turn_host.session_agents.DefaultHeadlessBuild",
        default_headless_build_stub(lambda **kwargs: _FakeAgent(**kwargs)),
    )
    return SessionAgentPool(console=Console(force_terminal=False))


def test_same_session_turns_do_not_interleave(monkeypatch: pytest.MonkeyPatch) -> None:
    """One agent per session is shared, so turns for it must not overlap.

    ``agent_for`` rebinds the cached agent's session and live sink. If a second
    turn for the same session rebinds while the first is still dispatching, the
    first turn's remaining output goes to the second turn's sink — a reply
    delivered to the wrong conversation.
    """
    # Arrange: the first turn holds the agent while the second tries to take it.
    import threading

    pool = _fake_agent_pool(monkeypatch)
    logger = logging.getLogger("test.pool")
    session = SessionCore(store=InMemorySessionStore())
    overlapped = threading.Event()
    first_inside = threading.Event()
    order: list[str] = []

    def _first() -> None:
        with pool.session_agent(session=session, output=MagicMock(), logger=logger):
            order.append("first-enter")
            first_inside.set()
            # Without serialization the second thread enters during this wait.
            if overlapped.wait(timeout=0.5):
                order.append("OVERLAP")
            order.append("first-exit")

    def _second() -> None:
        first_inside.wait(timeout=1.0)
        with pool.session_agent(session=session, output=MagicMock(), logger=logger):
            order.append("second-enter")
            overlapped.set()

    threads = [threading.Thread(target=_first), threading.Thread(target=_second)]

    # Act
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    # Assert
    assert "OVERLAP" not in order, order
    assert order.index("first-exit") < order.index("second-enter"), order


def test_pool_waits_for_a_session_lease_held_by_another_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Gateway and CLI turns must share the same whole-session lease."""
    import threading

    monkeypatch.setattr(
        "infrastructure.turn_host.session_lock.sessions_dir",
        lambda: tmp_path,
    )
    pool = _fake_agent_pool(monkeypatch)
    logger = logging.getLogger("test.pool.cross-host")
    session = SessionCore(store=InMemorySessionStore())
    attempted = threading.Event()
    entered = threading.Event()

    def _enter_pool() -> None:
        attempted.set()
        with pool.session_agent(session=session, output=MagicMock(), logger=logger):
            entered.set()

    with session_execution_lock(session.session_id):
        thread = threading.Thread(target=_enter_pool)
        thread.start()
        assert attempted.wait(timeout=1)
        assert not entered.wait(timeout=0.2)

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert entered.is_set()


def test_session_execution_lock_is_reentrant_on_one_thread(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A host and its pool can hold the same session lease without deadlocking."""
    monkeypatch.setattr(
        "infrastructure.turn_host.session_lock.sessions_dir",
        lambda: tmp_path,
    )
    session = SessionCore(store=InMemorySessionStore())

    # A zero timeout proves this reuses the held physical lock rather than
    # taking a second contending lock instance.
    with (
        session_execution_lock(session.session_id),
        session_execution_lock(session.session_id, timeout=0, reentrant=True),
    ):
        pass


def test_retained_reentrant_lease_releases_before_its_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A same-session /resume cannot fail while the turn unwinds its leases."""
    monkeypatch.setattr(
        "infrastructure.turn_host.session_lock.sessions_dir",
        lambda: tmp_path,
    )
    session = SessionCore(store=InMemorySessionStore())

    # This is the same nesting order as TurnRunner: the retained reentrant
    # target lease exits first, while the source's physical lease is still
    # registered with the current thread.
    with session_execution_lock(session.session_id), retained_session_execution_locks():
        assert retain_session_execution_lock(session.session_id, timeout=0, reentrant=True)


def test_pool_claims_session_lease_before_its_process_local_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct pool callers use the same lock order as TurnRunner."""
    from contextlib import contextmanager

    pool = _fake_agent_pool(monkeypatch)
    session = SessionCore(store=InMemorySessionStore())
    logger = logging.getLogger("test.pool.lock-order")
    entered: list[str] = []

    @contextmanager
    def _lease(_session_id: str, **_kwargs: object):
        entered.append("lease")
        yield

    class _LocalLock:
        def __enter__(self) -> None:
            entered.append("local")

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        "infrastructure.turn_host.session_agents.session_execution_lock",
        _lease,
    )
    monkeypatch.setattr(pool, "_lock_for", lambda _session_id: _LocalLock())

    with pool.session_agent(session=session, output=MagicMock(), logger=logger):
        assert entered == ["lease", "local"]


def test_pool_refreshes_session_after_acquiring_external_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A host waiting behind CLI resume must not run with stale session state."""
    import threading

    monkeypatch.setattr(
        "infrastructure.turn_host.session_lock.sessions_dir",
        lambda: tmp_path,
    )
    pool = _fake_agent_pool(monkeypatch)
    logger = logging.getLogger("test.pool.refresh-after-lease")
    session = SessionCore(store=InMemorySessionStore())
    session.cli_agent_messages = [("user", "stale request")]
    session.pending_user_choice = PendingUserChoice(
        title="Stale choice",
        options=("old",),
    )
    attempted = threading.Event()
    refreshed = threading.Event()

    def refresh_from_storage(_manager: object, target: SessionCore) -> SessionCore:
        target.cli_agent_messages = [("user", "resumed request")]
        target.pending_user_choice = PendingUserChoice(
            title="Fresh choice",
            options=("new",),
        )
        refreshed.set()
        return target

    monkeypatch.setattr(
        "infrastructure.turn_host.session_agents.SessionManager.refresh_from_storage",
        refresh_from_storage,
    )

    def _enter_pool() -> None:
        attempted.set()
        with pool.session_agent(session=session, output=MagicMock(), logger=logger):
            return

    with session_execution_lock(session.session_id):
        thread = threading.Thread(target=_enter_pool)
        thread.start()
        assert attempted.wait(timeout=1)
        assert not refreshed.wait(timeout=0.2)

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert refreshed.is_set()
    assert session.cli_agent_messages == [("user", "resumed request")]
    assert session.pending_user_choice is not None
    assert session.pending_user_choice.title == "Fresh choice"


def test_different_sessions_still_run_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serialization is per session — unrelated conversations must not queue."""
    # Arrange
    import threading

    pool = _fake_agent_pool(monkeypatch)
    logger = logging.getLogger("test.pool")
    both_inside = threading.Barrier(2, timeout=5)
    reached = []

    def _hold() -> None:
        session = SessionCore(store=InMemorySessionStore())
        with pool.session_agent(session=session, output=MagicMock(), logger=logger):
            # Times out if the pool serializes across unrelated sessions.
            both_inside.wait()
            reached.append(session.session_id)

    threads = [threading.Thread(target=_hold), threading.Thread(target=_hold)]

    # Act
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    # Assert
    assert len(reached) == 2, reached


def test_drop_session_removes_cached_agent_and_output(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _fake_agent_pool(monkeypatch)
    session = SessionCore(store=InMemorySessionStore())
    logger = logging.getLogger("test.pool.drop")
    first = pool.agent_for(session=session, output=MagicMock(), logger=logger)
    assert pool.cached_session_ids == frozenset({session.session_id})

    pool.drop_session(session.session_id)

    assert pool.cached_session_ids == frozenset()
    second = pool.agent_for(session=session, output=MagicMock(), logger=logger)
    assert second is not first


def test_retain_only_current_session_drops_stale_ids_after_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shell /new and /resume rotate session_id; the REPL keeps one TurnRunner."""
    constructed: list[str] = []

    class _FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            constructed.append(str(kwargs["session"].session_id))
            self.bind_turn = MagicMock()
            self.bind_session = MagicMock()

    monkeypatch.setattr(
        "infrastructure.turn_host.session_agents.DefaultHeadlessBuild",
        default_headless_build_stub(lambda **kwargs: _FakeAgent(**kwargs)),
    )
    pool = SessionAgentPool(
        console=Console(force_terminal=False),
        retain_only_current_session=True,
    )
    logger = logging.getLogger("test.pool.retain")
    first = SessionCore(store=InMemorySessionStore())
    second = SessionCore(store=InMemorySessionStore())

    pool.agent_for(session=first, output=MagicMock(), logger=logger)
    assert pool.cached_session_ids == frozenset({first.session_id})

    pool.agent_for(session=second, output=MagicMock(), logger=logger)
    assert pool.cached_session_ids == frozenset({second.session_id})
    assert constructed == [first.session_id, second.session_id]


def test_gateway_default_retains_multiple_session_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chat hosts many conversations on one TurnRunner — do not cull peers."""
    pool = _fake_agent_pool(monkeypatch)
    assert pool._retain_only_current_session is False
    logger = logging.getLogger("test.pool.gateway")
    a = SessionCore(store=InMemorySessionStore())
    b = SessionCore(store=InMemorySessionStore())
    pool.agent_for(session=a, output=MagicMock(), logger=logger)
    pool.agent_for(session=b, output=MagicMock(), logger=logger)
    assert pool.cached_session_ids == frozenset({a.session_id, b.session_id})

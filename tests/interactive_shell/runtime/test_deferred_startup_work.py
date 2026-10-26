"""Launch work held until the shell first waits on the user (the first menu draw)."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

import surfaces.interactive_shell.controller as controller_module
import surfaces.interactive_shell.main as main_entrypoint
from core.agent_harness.spi.session_state import PendingUserChoice
from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
from surfaces.interactive_shell.runtime.startup.deferred_work import DeferredStartupWork
from surfaces.interactive_shell.session import Session

_WAIT_SECONDS = 5.0
_CHOICE = PendingUserChoice(title="What would you like to do?", options=("Repair CI", "Skip"))


def _job(ran: list[str], name: str, done: threading.Event | None = None) -> Any:
    def run() -> None:
        ran.append(name)
        if done is not None:
            done.set()

    return run


def test_held_work_runs_once_in_order_only_after_release() -> None:
    work = DeferredStartupWork(settle_seconds=0)
    ran: list[str] = []
    done = threading.Event()
    work.defer("warm-up", _job(ran, "warm-up"))
    work.defer("snapshot", _job(ran, "snapshot", done))

    assert ran == []

    work.release()
    work.release()
    assert done.wait(_WAIT_SECONDS)
    assert ran == ["warm-up", "snapshot"]

    late = threading.Event()
    work.defer("late", _job(ran, "late", late))
    assert late.wait(_WAIT_SECONDS)
    assert ran == ["warm-up", "snapshot", "late"]


def test_a_failing_job_does_not_stop_the_ones_after_it() -> None:
    work = DeferredStartupWork(settle_seconds=0)
    ran: list[str] = []
    done = threading.Event()

    def fail() -> None:
        raise RuntimeError("snapshot backend down")

    work.defer("snapshot", fail)
    work.defer("warm-up", _job(ran, "warm-up", done))
    work.release()

    assert done.wait(_WAIT_SECONDS)
    assert ran == ["warm-up"]


def test_work_never_released_is_dropped_when_the_shell_closes() -> None:
    work = DeferredStartupWork(settle_seconds=0)
    ran: list[str] = []
    work.defer("warm-up", _job(ran, "warm-up"))

    work.close()
    work.release()
    work.defer("late", _job(ran, "late"))

    assert ran == []


class _SessionStore:
    def open_store(self, _session: object) -> None:
        return

    def refresh_from_storage(self, _session: object) -> None:
        return

    def flush(self, _session: object) -> None:
        return

    def close(self, _session: object, **_kwargs: object) -> None:
        return


def _boot(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    on_shell_start: Callable[[DeferredStartupWork], None],
) -> None:
    class _Controller:
        def __init__(
            self, *_args: object, startup_work: DeferredStartupWork, **_kw: object
        ) -> None:
            self._startup_work = startup_work

        async def start_interactive_shell(self) -> None:
            on_shell_start(self._startup_work)

    monkeypatch.setattr(main_entrypoint, "identify_saved_github_username", lambda: None)
    monkeypatch.setattr(
        main_entrypoint,
        "create_repl_runtime",
        lambda **_kwargs: SimpleNamespace(session=session, state=ReplState(), inbox=None),
    )
    monkeypatch.setattr(main_entrypoint, "InteractiveShellController", _Controller)
    monkeypatch.setattr(main_entrypoint.SessionManager, "for_session", lambda _s: _SessionStore())


def test_first_turn_warmup_waits_for_the_startup_menu_to_draw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The warm-up's SDK import must not compete with the first menu's paint."""
    session = Session()
    warmed = threading.Event()
    events: list[str] = []

    def offer(menu_session: Session, *_args: object, **_kwargs: object) -> bool:
        events.append("menu queued")
        menu_session.pending_user_choice = _CHOICE
        return True

    def shell_started(_work: DeferredStartupWork) -> None:
        assert not warmed.wait(0.3), "warm-up started before the menu drew"
        # What ``/choose`` does as the startup menu draws.
        session.terminal.release_startup_work()
        assert warmed.wait(_WAIT_SECONDS)

    _boot(monkeypatch, session, shell_started)
    monkeypatch.setattr(main_entrypoint, "offer_demo", offer)
    monkeypatch.setattr(main_entrypoint, "warm_first_turn", warmed.set)

    exit_code = asyncio.run(
        main_entrypoint.run_repl_async(tools_ready=lambda: events.append("tools ready"))
    )

    assert exit_code == 0
    # The startup turn cannot start before the prewarmed tool registry is ready.
    assert events == ["tools ready", "menu queued"]


def test_launch_without_a_startup_menu_leaves_the_release_to_the_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The controller releases once its jobs are held, so they all get the settle delay."""
    ran = threading.Event()

    def shell_started(work: DeferredStartupWork) -> None:
        work.defer("snapshot", ran.set)
        assert not ran.wait(0.3)
        work.release()
        assert ran.wait(_WAIT_SECONDS)

    _boot(monkeypatch, Session(), shell_started)
    monkeypatch.setattr(main_entrypoint, "offer_demo", lambda *_a, **_k: False)

    assert asyncio.run(main_entrypoint.run_repl_async()) == 0


def test_a_first_turn_that_never_drew_a_menu_still_releases_held_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed ``/choose`` must not leave the warm-up and snapshots held forever."""
    work = DeferredStartupWork(settle_seconds=0)
    ran = threading.Event()
    work.defer("snapshot", ran.set)

    async def failing_turn(_resources: object, _text: str) -> None:
        raise RuntimeError("turn failed before the menu drew")

    monkeypatch.setattr(controller_module, "run_agent_turn", failing_turn)
    controller = SimpleNamespace(turn_runtime=object(), startup_work=work)

    with pytest.raises(RuntimeError):
        asyncio.run(controller_module.InteractiveShellController._run_turn(controller, "/choose"))  # type: ignore[arg-type]

    assert ran.wait(_WAIT_SECONDS)


@pytest.mark.asyncio
async def test_launch_snapshots_go_to_the_deferral_instead_of_starting_with_the_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The GitHub snapshot imports the whole tool registry; it must wait for the menu."""
    import surfaces.interactive_shell.runtime.background.workers as workers

    started: list[str] = []
    monkeypatch.setattr(
        workers, "capture_github_connection_snapshot", lambda _s: started.append("snapshot")
    )
    monkeypatch.setattr(workers, "_restart_stale_scheduler", lambda: started.append("scheduler"))
    work = DeferredStartupWork(settle_seconds=0)
    state = ReplState()
    state.exit_requested = True
    pool = workers.BackgroundTaskPool(
        Session(), state, SpinnerState(), None, lambda: None, defer_thread_job=work.defer
    )

    async def idle() -> None:
        return

    tasks = pool.start_all(idle)
    await asyncio.gather(*(task for _label, task in tasks))
    await asyncio.sleep(0.05)

    assert [label for label, _task in tasks] == ["processor", "alert watcher", "spinner ticker"]
    assert started == []
    done = threading.Event()
    work.defer("marker", done.set)
    work.release()
    assert done.wait(_WAIT_SECONDS)
    # The scheduler check runs first: an exit during the snapshot must not skip it.
    assert started == ["scheduler", "snapshot"]


def test_tools_ready_returns_only_after_the_registry_load_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The registry caches are not single-flight: the first turn must not race the prewarm."""
    import time

    import surfaces.interactive_shell.runtime.startup.tool_registry_prewarm as prewarm

    loaded: list[str] = []

    def slow_load() -> frozenset[str]:
        time.sleep(0.2)
        loaded.append("registry")
        return frozenset()

    monkeypatch.setattr(prewarm, "registered_single_turn_tool_names", slow_load)

    prewarm.ToolRegistryPrewarm().start().wait()

    assert loaded == ["registry"]


def test_an_interrupted_warmup_drain_still_closes_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A teardown Ctrl+C in the drains must not carry past the session close.

    ``begin_ctrl_c_exit`` makes the next press raise wherever it lands, and the
    warm-up join is one of the places that blocks long enough to be hit.
    """
    closed: list[bool] = []

    def _interrupted_join() -> None:
        raise KeyboardInterrupt

    def _close(_session: object, _state: object, **_kwargs: object) -> None:
        closed.append(True)

    _boot(monkeypatch, Session(), lambda _work: None)
    monkeypatch.setattr(main_entrypoint, "offer_demo", lambda *_a, **_k: False)
    monkeypatch.setattr(main_entrypoint, "join_first_turn_warmup", _interrupted_join)
    monkeypatch.setattr(main_entrypoint, "close_repl_session", _close)

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(main_entrypoint.run_repl_async())

    assert closed == [True]

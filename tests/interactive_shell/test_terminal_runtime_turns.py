"""Turn-focused tests for interactive shell terminal runtime dispatch helpers."""

from __future__ import annotations

import asyncio
import contextlib
import io

import pytest
from rich.console import Console

from surfaces.interactive_shell.runtime import input_policy as loop_input_policy
from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
from surfaces.interactive_shell.runtime.core.turn_accounting import (
    ToolCallingTurnResult,
)
from surfaces.interactive_shell.runtime.turn_host import (
    AgentTurnResources,
    run_agent_turn,
    run_agent_turn_queue,
)
from surfaces.interactive_shell.session import Session
from tests.shared.harness_turn_driver import run_harness_turn


def test_turn_needs_exclusive_stdin_for_integration_list_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = Session()

    assert loop_input_policy.turn_needs_exclusive_stdin("/integrations", session) is False
    assert loop_input_policy.turn_needs_exclusive_stdin("/mcp", session) is False
    assert loop_input_policy.turn_needs_exclusive_stdin("/integrations list", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/mcp list", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/integrations ls", session) is False
    assert loop_input_policy.turn_needs_exclusive_stdin("/mcp ls", session) is False
    assert loop_input_policy.turn_needs_exclusive_stdin("/memory", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/model", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/loops", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/fleet", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/theme", session) is True

    # Typed bare `/model set` opens the provider picker.
    assert loop_input_policy.turn_needs_exclusive_stdin("/model set", session) is True
    # A subcommand that asks for its missing value needs stdin exactly as its
    # parent menu does; without this, typing it prints usage while choosing the
    # same row in /help (which reserves stdin for the whole turn) opens a picker.
    assert loop_input_policy.turn_needs_exclusive_stdin("/model toolcall", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/history retention", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/loops active", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/loops messages", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/loops show", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/loops show abc123", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/theme blue", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/verify", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/verify datadog", session) is False

    # Gating is literal-/slash only: bare command words are not recognized.
    assert loop_input_policy.turn_needs_exclusive_stdin("integrations", session) is False
    assert loop_input_policy.turn_needs_exclusive_stdin("integrations list", session) is False
    assert loop_input_policy.turn_needs_exclusive_stdin("verify", session) is False


@pytest.mark.parametrize(
    "command", ["/work list", "/work next", "/cron list", "/cron logs task-id", "/cron status"]
)
def test_responsive_lists_own_stdin(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    assert loop_input_policy.turn_needs_exclusive_stdin(command, Session())


def test_turn_needs_exclusive_stdin_for_exit_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = Session()

    assert loop_input_policy.turn_needs_exclusive_stdin("/exit", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/quit", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/logout", session) is True
    # Bare command words are not recognized under literal-/slash gating.
    assert loop_input_policy.turn_needs_exclusive_stdin("quit", session) is False


def test_turn_needs_exclusive_stdin_for_goal_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/goal set`` must finish before the condition autosubmits as ``[N] ❯``."""
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = Session()
    assert (
        loop_input_policy.turn_needs_exclusive_stdin(
            "/goal set --max-turns 4 count windows users",
            session,
        )
        is True
    )
    assert loop_input_policy.turn_needs_exclusive_stdin("/goal", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("goal set x", session) is False


def test_turn_needs_exclusive_stdin_for_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/update`` hits the network; block the next prompt until output is printed."""
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = Session()

    assert loop_input_policy.turn_needs_exclusive_stdin("/update", session) is True
    # Bare command words are not recognized under literal-/slash gating.
    assert loop_input_policy.turn_needs_exclusive_stdin("update", session) is False


def test_turn_needs_exclusive_stdin_for_integration_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = Session()

    assert loop_input_policy.turn_needs_exclusive_stdin("/integrations setup", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/mcp connect github", session) is True
    # Bare command words are not recognized under literal-/slash gating.
    assert (
        loop_input_policy.turn_needs_exclusive_stdin("integrations setup datadog", session) is False
    )


def test_turn_needs_exclusive_stdin_for_integration_remove(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``remove``/``disconnect`` drive a native inline picker that reads raw
    stdin; the REPL must block the next prompt so keystrokes and CPR responses
    do not leak into the prompt buffer."""
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = Session()

    assert loop_input_policy.turn_needs_exclusive_stdin("/integrations remove", session) is True
    assert (
        loop_input_policy.turn_needs_exclusive_stdin("/integrations remove github", session) is True
    )
    assert loop_input_policy.turn_needs_exclusive_stdin("/mcp disconnect", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/mcp disconnect github", session) is True
    # Bare command words are not recognized under literal-/slash gating.
    assert (
        loop_input_policy.turn_needs_exclusive_stdin("integrations remove github", session) is False
    )


def test_turn_needs_exclusive_stdin_for_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/health`` prints the Integration Checks Rich table."""
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = Session()

    assert loop_input_policy.turn_needs_exclusive_stdin("/health", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("health", session) is False


def test_turn_needs_exclusive_stdin_for_onboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/onboard`` is an interactive wizard; the REPL must wait for it to
    finish before reading the next prompt so the wizard subprocess has
    exclusive stdin and can drive its own questionary widgets.
    """
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = Session()

    assert loop_input_policy.turn_needs_exclusive_stdin("/onboard", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/setup", session) is True
    # Args don't change the exclusive-stdin requirement.
    assert loop_input_policy.turn_needs_exclusive_stdin("/onboard local_llm", session) is True
    # Bare command words are not recognized under literal-/slash gating.
    assert loop_input_policy.turn_needs_exclusive_stdin("onboard", session) is False


def test_turn_needs_exclusive_stdin_for_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/config`` delegates to a subprocess; block the next prompt until output
    is printed so config lines do not overlap the pinned input bar.
    """
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = Session()

    assert loop_input_policy.turn_needs_exclusive_stdin("/config", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/config show", session) is True
    assert (
        loop_input_policy.turn_needs_exclusive_stdin(
            "/config set interactive.layout pinned",
            session,
        )
        is True
    )


@pytest.mark.asyncio
async def test_queued_literal_quit_requests_runtime_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queued ``/quit`` must set ``exit_requested`` without blocking on analytics I/O."""
    # Match ``test_commands.py``: real ``/quit`` can flush PostHog; under xdist +
    # coverage that network drain has hung CI workers for the full job timeout.
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.exit_control._flush_analytics_on_exit",
        lambda _console: None,
    )
    from surfaces.interactive_shell.runtime.core.state import ReplState

    state = ReplState()
    session = Session()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

    async def _run_turn(text: str) -> None:
        await asyncio.to_thread(
            run_harness_turn,
            text,
            session,
            console,
            confirm_fn=None,
            is_tty=None,
            request_exit=state.request_exit,
        )

    worker = asyncio.create_task(run_agent_turn_queue(state=state, run_turn=_run_turn))
    try:
        await state.queue.put("/quit")
        # Deliberately no per-await deadline. A real turn takes ~1-2s, so a short
        # one loses the race under parallel CI load -- and losing it hangs rather
        # than fails: the TimeoutError unwinds into asyncio teardown, where
        # ``_cancel_all_tasks`` can park forever with the turn thread already
        # finished. That burned the 30-minute ``cli-runtime-3`` job budget with
        # no traceback. ``timeout`` in pytest.ini is the backstop for a genuine
        # deadlock; stubbing the analytics flush above keeps ``/quit`` off the
        # PostHog network path under xdist + coverage.
        await state.queue.join()
        _ = await worker
    finally:
        if not worker.done():
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                _ = await worker

    assert state.exit_requested is True


def test_turn_end_retries_auto_command_deferred_during_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ask-user ``/choose`` queued mid-turn must wake the open prompt."""

    async def _scenario() -> None:
        from surfaces.interactive_shell.runtime import shell_turn_execution

        session = Session()
        refresh_dispatch_states: list[bool] = []
        session.terminal.prompt_refresh_fn = lambda: refresh_dispatch_states.append(
            session.terminal.dispatch_active
        )

        def _queue_choose(*_args: object, **_kwargs: object) -> None:
            session.terminal.set_auto_command("/choose")

        monkeypatch.setattr(shell_turn_execution, "execute_shell_turn", _queue_choose)
        runtime = AgentTurnResources(
            session=session,
            state=ReplState(),
            spinner=SpinnerState(),
            invalidate_prompt=lambda: None,
            console=Console(file=io.StringIO(), force_terminal=False, highlight=False),
        )

        await run_agent_turn(runtime, "ask me to choose")

        assert session.terminal.pending_prompt_default == "/choose"
        assert refresh_dispatch_states == [True, False]

    asyncio.run(_scenario())


def test_cancelled_turn_does_not_block_asyncio_runner_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forced exit must not leave work that ``asyncio.run`` waits to join."""
    import threading

    from infrastructure.analytics.usage_context import get_session_id, get_surface
    from surfaces.interactive_shell.runtime import shell_turn_execution

    started = threading.Event()
    release = threading.Event()
    worker_context: dict[str, object] = {}
    runner_errors: list[BaseException] = []

    def _hold_turn(*_args: object, **_kwargs: object) -> None:
        worker_context.update(
            daemon=threading.current_thread().daemon,
            session_id=get_session_id(),
            surface=get_surface(),
        )
        started.set()
        release.wait()

    monkeypatch.setattr(shell_turn_execution, "execute_shell_turn", _hold_turn)
    session = Session()

    async def _scenario() -> None:
        runtime = AgentTurnResources(
            session=session,
            state=ReplState(),
            spinner=SpinnerState(),
            invalidate_prompt=lambda: None,
            console=Console(file=io.StringIO(), force_terminal=False, highlight=False),
        )
        task = asyncio.create_task(run_agent_turn(runtime, "blocking turn"))
        deadline = asyncio.get_running_loop().time() + 1
        while not started.is_set():
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError("turn worker did not start")
            await asyncio.sleep(0.001)
        task.cancel()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)

    def _run_scenario() -> None:
        try:
            asyncio.run(_scenario())
        except Exception as exc:
            runner_errors.append(exc)

    runner = threading.Thread(target=_run_scenario, name="test-asyncio-run", daemon=True)
    runner.start()
    try:
        runner.join(timeout=2)

        assert not runner.is_alive(), "asyncio.run waited for the blocked turn worker"
        assert runner_errors == []
        assert worker_context == {
            "daemon": True,
            "session_id": session.session_id,
            "surface": "cli",
        }
    finally:
        release.set()
        runner.join(timeout=1)


@pytest.mark.asyncio
async def test_cancelled_turn_does_not_spawn_another_worker_until_it_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated cancellation cannot accumulate raw threads behind a stuck turn."""
    import threading

    from surfaces.interactive_shell.runtime import shell_turn_execution

    first_started = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()
    calls = 0

    def _execute(*_args: object, **_kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            release_first.wait()
        else:
            second_started.set()

    monkeypatch.setattr(shell_turn_execution, "execute_shell_turn", _execute)
    runtime = AgentTurnResources(
        session=Session(),
        state=ReplState(),
        spinner=SpinnerState(),
        invalidate_prompt=lambda: None,
        console=Console(file=io.StringIO(), force_terminal=False, highlight=False),
    )
    first = asyncio.create_task(run_agent_turn(runtime, "first"))
    second: asyncio.Task[None] | None = None
    try:
        while not first_started.is_set():
            await asyncio.sleep(0.001)
        assert runtime.has_live_turn_worker()
        first.cancel()
        _ = await asyncio.gather(first, return_exceptions=True)
        assert runtime.has_live_turn_worker()

        second = asyncio.create_task(run_agent_turn(runtime, "second"))
        await asyncio.sleep(0.1)
        assert calls == 1
        assert not second_started.is_set()

        release_first.set()
        await asyncio.wait_for(second, timeout=1)
        assert calls == 2
        assert second_started.is_set()
        assert not runtime.has_live_turn_worker()
    finally:
        release_first.set()
        first.cancel()
        if second is not None:
            second.cancel()
        _ = await asyncio.gather(
            *(task for task in (first, second) if task is not None),
            return_exceptions=True,
        )


@pytest.mark.asyncio
async def test_cancelled_turn_runs_deferred_cleanup_after_its_worker_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import contextvars
    import threading

    from surfaces.interactive_shell.runtime import shell_turn_execution

    started = threading.Event()
    release = threading.Event()
    cleaned_up = threading.Event()
    callback_context = contextvars.ContextVar("callback_context", default="missing")
    observed_context: list[str] = []

    def _execute(*_args: object, **_kwargs: object) -> None:
        started.set()
        release.wait()

    def _cleanup() -> None:
        observed_context.append(callback_context.get())
        cleaned_up.set()

    monkeypatch.setattr(shell_turn_execution, "execute_shell_turn", _execute)
    runtime = AgentTurnResources(
        session=Session(),
        state=ReplState(),
        spinner=SpinnerState(),
        invalidate_prompt=lambda: None,
        console=Console(file=io.StringIO(), force_terminal=False, highlight=False),
    )
    task = asyncio.create_task(run_agent_turn(runtime, "blocked"))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        _ = await asyncio.gather(task, return_exceptions=True)

        callback_context.set("registration")
        runtime.run_after_turn_worker(_cleanup)
        assert not cleaned_up.is_set()

        release.set()
        assert await asyncio.to_thread(cleaned_up.wait, 1)
        assert observed_context == ["registration"]
    finally:
        release.set()
        task.cancel()
        _ = await asyncio.gather(task, return_exceptions=True)


def test_run_harness_turn_nitro_prompt_uses_cli_agent_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nitro_prompt = (
        "I want to deploy OpenSRE on a remote EC2 Nitro instance, and then I want to send\n"
        'it an investigation. Can you please deploy the instance and send it "hello world"?'
    )
    action_calls: list[str] = []

    def _fake_execute_cli_actions(
        text: str,
        _session: Session,
        _console: Console,
        **kwargs: object,
    ) -> ToolCallingTurnResult:
        action_calls.append(text)
        return ToolCallingTurnResult(
            planned_count=2,
            executed_count=2,
            executed_success_count=2,
            has_unhandled_clause=False,
            handled=True,
        )

    session = Session()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    run_harness_turn(
        nitro_prompt,
        session,
        console,
        confirm_fn=None,
        is_tty=None,
        execute_actions=_fake_execute_cli_actions,
    )

    assert action_calls == [nitro_prompt]


class TestDispatchSpinnerBehavior:
    @pytest.mark.parametrize(
        "text",
        [
            "/history",
            "/tasks",
            "/model show",
        ],
    )
    def test_slash_dispatches_do_not_show_assistant_spinner(self, text: str) -> None:
        assert loop_input_policy.turn_should_show_spinner(text, Session()) is False

    @pytest.mark.parametrize(
        "text",
        [
            "why did this fail?",
            "explain deploy",
            # Bare command words and opensre passthrough are no longer treated as
            # literal commands, so the spinner shows while the planner runs.
            "tasks",
            "help",
            "opensre fleet scan",
        ],
    )
    def test_non_slash_dispatches_show_assistant_spinner(self, text: str) -> None:
        assert loop_input_policy.turn_should_show_spinner(text, Session()) is True

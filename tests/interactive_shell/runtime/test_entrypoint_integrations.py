"""Tests for hydrating configured integrations onto the REPL session at boot.

Without this the agent cannot answer "is X installed?" and the integration
guards stay dead because ``configured_integrations_known`` never flips to True.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from rich.console import Console

import surfaces.interactive_shell.main as main_entrypoint
from core.agent_harness.session.integration_resolution import IntegrationResolutionResult
from surfaces.interactive_shell.session import Session


def test_hydrate_populates_session_from_effective_resolution(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "infrastructure.harness_providers.configured_integration_services",
        lambda: ["gitlab", "datadog"],
    )
    session = Session()
    session.hydrate_configured_integrations()
    assert session.configured_integrations_known is True
    # Metadata discovery covers env + local store and is returned in sorted order.
    assert session.configured_integrations == ("datadog", "gitlab")


def test_hydrate_marks_known_even_when_none_configured(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "infrastructure.harness_providers.configured_integration_services",
        list,
    )
    session = Session()
    session.hydrate_configured_integrations()
    assert session.configured_integrations_known is True
    assert session.configured_integrations == ()


def test_warm_resolved_integrations_populates_cache(monkeypatch: Any) -> None:
    resolved = {"datadog": {"site": "datadoghq.com"}, "grafana": {"url": "http://localhost"}}
    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        lambda: resolved,
    )
    session = Session()
    session.warm_resolved_integrations()
    assert session.resolved_integrations_cache == resolved


def test_warm_resolved_integrations_is_idempotent(monkeypatch: Any) -> None:
    calls: list[str] = []

    def _resolve() -> dict[str, Any]:
        calls.append("resolve")
        return {"github": {}}

    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        _resolve,
    )
    session = Session()
    session.warm_resolved_integrations()
    session.warm_resolved_integrations()
    assert calls == ["resolve"]


def test_warm_resolved_integrations_skips_empty_cache(monkeypatch: Any) -> None:
    calls: list[str] = []

    def _resolve() -> dict[str, Any]:
        calls.append("resolve")
        return {}

    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        _resolve,
    )
    session = Session()
    session.warm_resolved_integrations()
    assert session.resolved_integrations_cache is None
    session.warm_resolved_integrations()
    assert calls == ["resolve", "resolve"]


def test_get_integrations_returns_pydantic_cached_result(monkeypatch: Any) -> None:
    def _unexpected_resolve() -> dict[str, Any]:
        raise AssertionError("cached integrations should not re-resolve")

    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        _unexpected_resolve,
    )
    session = Session()
    session.resolved_integrations_cache = {"datadog": {"site": "datadoghq.com"}}

    result = session.get_integrations()

    assert isinstance(result, IntegrationResolutionResult)
    assert result.resolved_integrations == {"datadog": {"site": "datadoghq.com"}}
    assert result.services == ("datadog",)


def test_get_integrations_respects_explicit_empty_cache(monkeypatch: Any) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        lambda: calls.append("resolve") or {"datadog": {}},
    )
    session = Session()
    session.resolved_integrations_cache = {}

    result = session.get_integrations()

    assert result.resolved_integrations == {}
    assert calls == []


def test_get_integrations_warms_metadata_only_cache(monkeypatch: Any) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        lambda: calls.append("resolve") or {"datadog": {"site": "datadoghq.com"}},
    )
    session = Session()
    session.resolved_integrations_cache = {"_gateway_chat_id": "chat-1"}

    result = session.get_integrations()

    assert calls == ["resolve"]
    assert result.resolved_integrations == {
        "_gateway_chat_id": "chat-1",
        "datadog": {"site": "datadoghq.com"},
    }


def test_stale_background_warm_does_not_overwrite_refreshed_cache() -> None:
    session = Session()
    stale_generation = session.integrations._warm_generation
    session.integrations._warm_generation += 1
    session.integrations._store(
        {"fresh": {"token": "new"}}, generation=session.integrations._warm_generation, stamp=0
    )
    session.integrations._store({"stale": {"token": "old"}}, generation=stale_generation, stamp=0)
    assert session.resolved_integrations_cache == {"fresh": {"token": "new"}}


def test_hydrate_entrypoint_does_not_warm_before_prompt(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "infrastructure.harness_providers.configured_integration_services",
        lambda: ["datadog"],
    )
    resolve_calls: list[str] = []

    def _resolve() -> dict[str, Any]:
        resolve_calls.append("resolve")
        return {"datadog": {"site": "datadoghq.com"}}

    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        _resolve,
    )
    session = Session()
    session.hydrate_configured_integrations()
    assert session.configured_integrations_known is True
    assert session.resolved_integrations_cache is None
    assert resolve_calls == []


def test_hydrate_leaves_unknown_on_failure(monkeypatch: Any) -> None:
    def _boom() -> list[str]:
        raise RuntimeError("catalog blew up")

    monkeypatch.setattr(
        "infrastructure.harness_providers.configured_integration_services",
        _boom,
    )
    session = Session()
    session.hydrate_configured_integrations()
    assert session.configured_integrations_known is False
    assert session.configured_integrations == ()


def test_run_repl_async_identifies_saved_github_username(monkeypatch: Any) -> None:
    identified: list[str] = []
    monkeypatch.setattr(
        main_entrypoint,
        "identify_saved_github_username",
        lambda: identified.append("called"),
    )

    def _run_initial_input(*_args: Any, **_kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(main_entrypoint, "run_initial_input", _run_initial_input)

    monkeypatch.setattr(
        main_entrypoint,
        "create_repl_runtime",
        lambda **_kwargs: SimpleNamespace(session=Session(), inbox=None),
    )

    import asyncio

    asyncio.run(main_entrypoint.run_repl_async(initial_input="hello"))

    assert identified == ["called"]


def test_run_repl_async_failed_resume_flushes_starter_session(
    monkeypatch: Any, tmp_path: Path
) -> None:
    import asyncio

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.setattr(
        "infrastructure.analytics.github_identity.identify_saved_github_username",
        lambda: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.session_cmds.resume.resume_session_by_prefix",
        lambda *_args, **_kwargs: False,
    )

    session = Session()
    flushed: list[str] = []
    original_flush = session.store.flush

    def _track_flush(current_session: Session) -> None:
        flushed.append(current_session.session_id)
        original_flush(current_session)

    monkeypatch.setattr(session.store, "flush", _track_flush)

    monkeypatch.setattr(
        main_entrypoint,
        "create_repl_runtime",
        lambda **_kwargs: SimpleNamespace(session=session, inbox=None),
    )

    exit_code = asyncio.run(main_entrypoint.run_repl_async(resume_session_id="missing-session"))

    assert exit_code == 1
    assert flushed == [session.session_id]
    assert not (sessions_dir / f"{session.session_id}.jsonl").exists()


def test_run_repl_async_runs_held_back_launch_work_once_the_banner_is_painted(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """``after_banner`` runs after ``finish_banner`` and before the session body."""
    import asyncio

    # Arrange: a booted runtime whose resume fails, so the run ends right after
    # the banner step without starting the prompt loop.
    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.setattr(
        "infrastructure.analytics.github_identity.identify_saved_github_username",
        lambda: None,
    )
    order: list[str] = []
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.session_cmds.resume.resume_session_by_prefix",
        lambda *_args, **_kwargs: order.append("resume") or False,
    )
    monkeypatch.setattr(
        main_entrypoint,
        "create_repl_runtime",
        lambda **_kwargs: SimpleNamespace(session=Session(), inbox=None),
    )

    # Act
    asyncio.run(
        main_entrypoint.run_repl_async(
            resume_session_id="missing-session",
            finish_banner=lambda: order.append("banner"),
            after_banner=lambda: order.append("after_banner"),
        )
    )

    # Assert
    assert order == ["banner", "after_banner", "resume"]


def _finish_banner_then_stop(**kwargs: Any) -> int:
    """Invoke the launch-banner finisher the real ``run_repl_async`` would.

    ``run_repl`` spins the wordmark on a thread and paints the static banner
    only when the async half calls ``finish_banner``. Stubs that skip the
    shell must still run that finisher or startup chrome never reaches the
    injected console.
    """
    finish = kwargs.get("finish_banner")
    if finish is not None:
        finish()
    return 0


def test_run_repl_writes_startup_output_to_the_supplied_console(monkeypatch: Any) -> None:
    """An embedding caller can capture the shell's output.

    The module built one forced-terminal Console at import and used it for the
    ready box, so nothing could redirect it.
    """
    # Arrange
    from io import StringIO

    from rich.console import Console

    from config.repl_config import ReplConfig

    captured = Console(file=StringIO(), force_terminal=False, width=80)
    monkeypatch.setattr(main_entrypoint.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main_entrypoint, "animate_launch_wordmark", lambda *_a, **_k: None)

    def _fake_terminal_ui(console: Any, **_kwargs: Any) -> None:
        console.print("SPLASH")
        console.print("READY")

    async def _skip_async(**kwargs: Any) -> int:
        return _finish_banner_then_stop(**kwargs)

    monkeypatch.setattr(main_entrypoint, "render_terminal_ui", _fake_terminal_ui)
    monkeypatch.setattr(main_entrypoint, "run_repl_async", _skip_async)

    # Act
    exit_code = main_entrypoint.run_repl(
        config=ReplConfig(enabled=True, layout="classic"),
        console=captured,
    )

    # Assert
    assert exit_code == 0
    text = captured.file.getvalue()  # type: ignore[attr-defined]
    assert "SPLASH" in text
    assert "READY" in text


def test_launch_banner_captures_shell_render_after_successful_first_paint(
    monkeypatch: Any,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(main_entrypoint, "animate_launch_wordmark", lambda *_a, **_k: None)
    monkeypatch.setattr(
        main_entrypoint,
        "render_terminal_ui",
        lambda *_a, **_k: events.append("rendered"),
    )
    monkeypatch.setattr(
        main_entrypoint,
        "capture_interactive_shell_rendered",
        lambda **_kwargs: events.append("captured"),
    )

    finish = main_entrypoint._start_launch_banner(
        Console(file=io.StringIO(), force_terminal=False),
        on_painted=lambda: events.append("captured"),
    )
    finish()

    assert events == ["rendered", "captured"]

    assert events == ["rendered", "captured"]


def test_run_repl_defaults_to_the_module_console(monkeypatch: Any) -> None:
    """Omitting the console keeps today's behaviour, not a silent no-op."""
    # Arrange
    seen: list[object] = []
    monkeypatch.setattr(main_entrypoint.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main_entrypoint, "animate_launch_wordmark", lambda *_a, **_k: None)

    def _record_console(console: Any, **_kwargs: Any) -> None:
        seen.append(console)

    async def _skip_async(**kwargs: Any) -> int:
        return _finish_banner_then_stop(**kwargs)

    monkeypatch.setattr(main_entrypoint, "render_terminal_ui", _record_console)
    monkeypatch.setattr(main_entrypoint, "run_repl_async", _skip_async)

    from config.repl_config import ReplConfig

    # Act
    main_entrypoint.run_repl(config=ReplConfig(enabled=True, layout="classic"))

    # Assert
    assert seen == [main_entrypoint._DEFAULT_CONSOLE]


def test_run_repl_async_routes_the_console_into_resume(monkeypatch: Any, tmp_path: Path) -> None:
    """The async half must use the caller's console too, not the module default.

    ``run_repl`` renders the welcome panel before the event loop; everything after —
    resume output and the controller — runs inside ``run_repl_async``, so the
    console has to survive the hand-off.
    """
    # Arrange
    import asyncio
    from io import StringIO

    from rich.console import Console

    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.setattr(
        "infrastructure.analytics.github_identity.identify_saved_github_username", lambda: None
    )

    seen: list[object] = []

    def _resume(_prefix: Any, _session: Any, console: Any, **_kwargs: Any) -> bool:
        seen.append(console)
        return False

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.session_cmds.resume.resume_session_by_prefix",
        _resume,
    )

    monkeypatch.setattr(
        main_entrypoint,
        "create_repl_runtime",
        lambda **_kwargs: SimpleNamespace(session=Session(), inbox=None),
    )
    captured = Console(file=StringIO(), force_terminal=False, width=80)

    # Act
    exit_code = asyncio.run(
        main_entrypoint.run_repl_async(resume_session_id="missing", console=captured)
    )

    # Assert
    assert exit_code == 1
    assert seen == [captured]


def test_run_repl_hands_its_console_to_the_async_half(monkeypatch: Any) -> None:
    """The console must survive the sync-to-async hand-off.

    ``run_repl`` renders the welcome panel itself and then delegates everything else;
    dropping the argument there would silently return output to the module
    default while the startup renders still looked correct.
    """
    # Arrange
    from io import StringIO

    from rich.console import Console

    from config.repl_config import ReplConfig

    monkeypatch.setattr(main_entrypoint.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main_entrypoint, "render_terminal_ui", lambda _console, **_kw: None)

    seen: list[object] = []

    async def _fake_async(**kwargs: Any) -> int:
        seen.append(kwargs.get("console"))
        return 0

    monkeypatch.setattr(main_entrypoint, "run_repl_async", _fake_async)
    captured = Console(file=StringIO(), force_terminal=False, width=80)

    # Act
    exit_code = main_entrypoint.run_repl(
        config=ReplConfig(enabled=True, layout="classic"), console=captured
    )

    # Assert
    assert exit_code == 0
    assert seen == [captured]


def test_package_facade_exposes_every_runtime_parameter() -> None:
    """``surfaces.interactive_shell.run_repl`` is the seam embedders import.

    It repeats the runtime signature rather than erasing it to ``*args``, which
    keeps type checking but means a parameter added to the runtime and not here
    is unreachable — ``console=`` was, so redirecting shell output raised
    TypeError through the public import.
    """
    # Arrange
    import inspect

    from surfaces.interactive_shell import run_repl as facade

    runtime = main_entrypoint.run_repl

    # Act
    facade_params = inspect.signature(facade).parameters
    runtime_params = inspect.signature(runtime).parameters

    # Assert
    assert set(runtime_params) <= set(facade_params), (
        "runtime parameters missing from the package facade: "
        f"{sorted(set(runtime_params) - set(facade_params))}"
    )


def test_console_injection_works_through_the_package_facade(monkeypatch: Any) -> None:
    """Capturing shell output must work via the documented import path."""
    # Arrange
    from io import StringIO

    from rich.console import Console

    from config.repl_config import ReplConfig
    from surfaces.interactive_shell import run_repl as facade

    monkeypatch.setattr(main_entrypoint.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main_entrypoint, "animate_launch_wordmark", lambda *_a, **_k: None)

    def _fake_terminal_ui(console: Any, **_kwargs: Any) -> None:
        console.print("SPLASH")

    async def _skip_async(**kwargs: Any) -> int:
        return _finish_banner_then_stop(**kwargs)

    monkeypatch.setattr(main_entrypoint, "render_terminal_ui", _fake_terminal_ui)
    monkeypatch.setattr(main_entrypoint, "run_repl_async", _skip_async)
    captured = Console(file=StringIO(), force_terminal=False, width=80)

    # Act
    exit_code = facade(config=ReplConfig(enabled=True, layout="classic"), console=captured)

    # Assert
    assert exit_code == 0
    assert "SPLASH" in captured.file.getvalue()  # type: ignore[attr-defined]


def test_initial_input_replay_uses_the_supplied_console(monkeypatch: Any) -> None:
    """Scripted replay must honour the caller's console too.

    ``run_repl_async`` returns through ``run_initial_input`` before the shell
    ever starts, and that function built its own forced-terminal Console — so
    an embedding caller got every replayed line on stdout instead of its own
    stream, while the interactive path looked correctly wired.
    """
    # Arrange
    import asyncio
    from io import StringIO

    from rich.console import Console

    from surfaces.interactive_shell.runtime.startup import initial_input as replay

    monkeypatch.setattr(
        "infrastructure.analytics.github_identity.identify_saved_github_username", lambda: None
    )

    def _fake_terminal_ui(console: Any, **_kwargs: Any) -> None:
        console.print("REPLAY-SPLASH")

    monkeypatch.setattr(replay, "render_terminal_ui", _fake_terminal_ui)

    # Rendering happens before the first turn; stub the turn so this pins the
    # console wiring rather than the whole execution stack.
    monkeypatch.setattr(replay, "render_submitted_prompt", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.shell_turn_execution.execute_shell_turn",
        lambda *_a, **_kw: None,
    )

    monkeypatch.setattr(
        main_entrypoint,
        "create_repl_runtime",
        lambda **_kwargs: SimpleNamespace(session=Session(), inbox=None),
    )
    captured = Console(file=StringIO(), force_terminal=False, width=80)

    # Act
    asyncio.run(main_entrypoint.run_repl_async(initial_input="/exit", console=captured))

    # Assert
    assert "REPLAY-SPLASH" in captured.file.getvalue()  # type: ignore[attr-defined]


def test_turn_output_and_prompt_echo_reach_the_supplied_console() -> None:
    """The conversation itself must land in the caller's stream.

    Startup renders honoured the injected console while the controller and turn
    host built their own force-terminal consoles, so an embedding caller
    captured the welcome panel and missed every agent response, tool line and prompt
    echo — the part it actually wanted.
    """
    # Arrange
    import threading
    from io import StringIO

    from rich.console import Console

    from surfaces.interactive_shell.runtime.turn_host import (
        AgentTurnResources,
        _streaming_console,
    )

    captured = Console(file=StringIO(), force_terminal=False, width=80)
    runtime = AgentTurnResources(
        session=Session(),
        state=SimpleNamespace(),
        spinner=SimpleNamespace(streaming=False, bytes_in=0),
        invalidate_prompt=lambda: None,
        console=captured,
    )

    # Act
    turn_console = _streaming_console(runtime, threading.Event())
    turn_console.print("AGENT-RESPONSE")

    # Assert
    assert captured.file.getvalue() == turn_console.file.getvalue()  # type: ignore[attr-defined]
    assert "AGENT-RESPONSE" in turn_console.file.getvalue()  # type: ignore[attr-defined]


def test_turn_output_reaches_capture_and_record_on_the_supplied_console() -> None:
    """Writing to the caller's *stream* is not the same as writing to its console.

    ``capture()`` and ``record`` buffer inside the ``Console`` object, not at its
    file, so a turn that renders to a copy of ``console.file`` leaves both empty:
    a caller wrapping a turn in ``capture()`` gets back nothing, and a recorded
    transcript silently omits every agent response.
    """
    # Arrange
    import threading
    from io import StringIO

    from rich.console import Console

    from surfaces.interactive_shell.runtime.turn_host import (
        AgentTurnResources,
        _streaming_console,
    )

    def build_runtime(console: Console) -> AgentTurnResources:
        return AgentTurnResources(
            session=Session(),
            state=SimpleNamespace(),
            spinner=SimpleNamespace(streaming=False, bytes_in=0),
            invalidate_prompt=lambda: None,
            console=console,
        )

    capturing = Console(file=StringIO(), force_terminal=False, width=80)
    recording = Console(file=StringIO(), force_terminal=False, width=80, record=True)

    # Act
    with capturing.capture() as capture:
        _streaming_console(build_runtime(capturing), threading.Event()).print("CAPTURED-RESPONSE")
    _streaming_console(build_runtime(recording), threading.Event()).print("RECORDED-RESPONSE")

    # Assert
    assert "CAPTURED-RESPONSE" in capture.get()
    assert "RECORDED-RESPONSE" in recording.export_text()


def test_turn_output_falls_back_to_the_shell_terminal() -> None:
    """With no console supplied the turn keeps today's forced-terminal output."""
    # Arrange
    import threading

    from surfaces.interactive_shell.runtime.turn_host import (
        AgentTurnResources,
        _streaming_console,
    )

    runtime = AgentTurnResources(
        session=Session(),
        state=SimpleNamespace(),
        spinner=SimpleNamespace(streaming=False, bytes_in=0),
        invalidate_prompt=lambda: None,
    )

    # Act
    turn_console = _streaming_console(runtime, threading.Event())

    # Assert
    assert turn_console.is_terminal is True


def test_controller_routes_its_console_to_echo_and_turns() -> None:
    """The controller must hand its console to both output paths.

    It builds several consoles; only ``service_console`` honoured injection, so
    prompt echoes and agent turns still went to the shell's own terminal while
    the caller captured nothing of the conversation.
    """
    # Arrange
    from io import StringIO

    from rich.console import Console

    from surfaces.interactive_shell.controller import InteractiveShellController

    captured = Console(file=StringIO(), force_terminal=False, width=80)

    # Act
    controller = InteractiveShellController(Session(), console=captured)

    # Assert
    assert controller.service_console is captured
    assert controller.echo_console is captured
    assert controller.turn_runtime.console is captured


def test_progress_display_uses_the_supplied_console(
    monkeypatch: Any,
) -> None:
    """Progress and tool-detail lines must follow the console too.

    The renderer honouring an injected console is not enough: it builds a
    ``ProgressTracker`` whose event-log display owned a separate ``Console``, so
    stage progress and every tool-detail line still went to the shell terminal.
    """
    # Arrange
    from surfaces.shared.terminal.output import tracker as tracker_module

    captured = Console(file=io.StringIO(), force_terminal=False, width=80)
    monkeypatch.setattr(tracker_module, "_repl_progress_active", lambda: True)
    monkeypatch.setattr(tracker_module, "_is_silent_output", lambda: False)
    monkeypatch.setattr(tracker_module, "get_output_format", lambda: "rich")
    monkeypatch.setattr(tracker_module, "register_tool_detail_toggle", lambda _fn: None)

    # Act
    tracker = tracker_module.ProgressTracker(console=captured)

    # Assert
    assert tracker._display is not None
    assert tracker._display._console is captured  # type: ignore[attr-defined]

"""Public REPL entrypoints."""

from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Callable

import click
from rich.console import Console

from config.repl_config import ReplConfig
from core.agent_harness import SessionManager
from infrastructure.analytics.github_identity import identify_saved_github_username
from infrastructure.logging import install_shell_log_handler, quiet_noisy_third_party_loggers
from infrastructure.terminal.theme import set_active_theme
from surfaces.interactive_shell.controller import InteractiveShellController
from surfaces.interactive_shell.runtime.context import create_repl_runtime
from surfaces.interactive_shell.runtime.startup.account_gate import (
    pass_sign_in_gate,
)
from surfaces.interactive_shell.runtime.startup.initial_input import run_initial_input
from surfaces.interactive_shell.runtime.startup.loop_suggestions import offer_loop_suggestions
from surfaces.interactive_shell.ui.terminal_ui import render_terminal_ui
from surfaces.shared.terminal.banner import animate_launch_wordmark
from surfaces.shared.terminal.components.rendering import repl_clear_screen

# Fallback when a caller does not supply one. Forces a terminal because the
# shell owns the screen; an embedding caller passes its own instead.
_DEFAULT_CONSOLE = Console(
    highlight=False, force_terminal=True, color_system="truecolor", legacy_windows=False
)


async def run_repl_async(
    initial_input: str | None = None,
    config: ReplConfig | None = None,
    resume_session_id: str | None = None,
    console: Console | None = None,
    cli_command_group: click.Command | None = None,
    finish_banner: Callable[[], None] | None = None,
    after_banner: Callable[[], None] | None = None,
) -> int:
    """Run the shell on an existing event loop and return its exit code.

    ``cli_command_group`` is the ``opensre`` Click group the shell documents to
    the model; the process entrypoint passes it, embedders may leave it out.
    ``after_banner`` is launch work the CLI held back until the banner is on
    screen (error-reporting start); it runs once the runtime is booted.
    """
    # Keep MCP schema-cache warnings / httpx chatter off the transcript —
    # progress is soft status lines, not library WARNINGs.
    quiet_noisy_third_party_loggers()
    identify_saved_github_username()

    cfg = config or ReplConfig.load()
    set_active_theme(cfg.theme)
    out = console or _DEFAULT_CONSOLE
    # WARNING+ records print through the shell console, so one emitted from a
    # probe thread while a status spinner animates lands whole above it instead
    # of racing the spinner's redraw on the tty and staircasing what follows.
    install_shell_log_handler(lambda: out)
    # Let PromptBuilder build the prompt session so it can wire the
    # composer-hide (needs the session + REPL state, which do not exist yet).
    runtime_context = create_repl_runtime()
    session = runtime_context.session
    session.terminal.cli_command_group = cli_command_group

    if initial_input:
        if after_banner is not None:
            after_banner()
        session.warm_resolved_integrations()
        return run_initial_input(initial_input, session, out)

    # The sign-in gate runs once, in the synchronous ``run_repl`` entrypoint,
    # where it interleaves with the launch-banner paint. This coroutine is the
    # shell body only; embedders driving it directly manage their own auth.

    # Open the session file now that we know this is an interactive REPL run.
    SessionManager.for_session(session).open_store(session)
    # The runtime is booted; nothing has printed yet. Stop the launch spin and
    # paint the static banner before anything below can write to the screen.
    if finish_banner is not None:
        finish_banner()
    # The launch has nothing left to load: held-back work no longer competes
    # with it for the interpreter.
    if after_banner is not None:
        after_banner()

    try:
        if resume_session_id:
            from surfaces.interactive_shell.command_registry.session_cmds.resume import (
                resume_session_by_prefix,
            )

            slash_command = f"/resume {resume_session_id.strip()}"
            if not resume_session_by_prefix(
                resume_session_id.strip(),
                session,
                out,
                slash_command=slash_command,
            ):
                return 1
        else:
            # Fresh interactive start with no scheduled loops: offer the
            # suggested-loops picker before the prompt loop takes stdin.
            offer_loop_suggestions(session, out)

        await InteractiveShellController(
            runtime_context,
            config=cfg,
            console=out,
        ).start_interactive_shell()
        return 0
    finally:
        # True end-of-run teardown: persist and release the session's resources.
        SessionManager.for_session(session).close(session)


def _start_launch_banner(console: Console) -> Callable[[], None]:
    """Spin the wordmark on a thread while the runtime boots; return the finisher.

    The finisher stops the spin (after its minimum frames), waits for it, and
    prints the static banner — call it before anything else writes to the
    screen. Off a TTY the spin is a no-op and only the static banner prints.
    """
    stop = threading.Event()
    spinner = threading.Thread(
        target=animate_launch_wordmark,
        args=(console,),
        kwargs={"stop": stop},
        name="launch-banner-spin",
        daemon=True,
    )
    spinner.start()

    def finish() -> None:
        stop.set()
        spinner.join()
        render_terminal_ui(console, animate=False)

    return finish


def run_repl(
    initial_input: str | None = None,
    config: ReplConfig | None = None,
    *,
    resume_session_id: str | None = None,
    console: Console | None = None,
    cli_command_group: click.Command | None = None,
    after_banner: Callable[[], None] | None = None,
) -> int:
    """Run the shell on a new event loop and return its exit code."""
    cfg = config or ReplConfig.load()
    set_active_theme(cfg.theme)
    out = console or _DEFAULT_CONSOLE
    if not cfg.enabled and not resume_session_id:
        return 0
    if not sys.stdin.isatty() and initial_input is None:
        return 0

    finish_banner: Callable[[], None] | None = None
    try:
        if not initial_input:
            if not pass_sign_in_gate(out):
                return 0
            # Wipe the calling shell or completed sign-in screen so the REPL
            # reads as its own screen, then boot it under the launch animation.
            repl_clear_screen()
            finish_banner = _start_launch_banner(out)

        return asyncio.run(
            run_repl_async(
                initial_input=initial_input,
                config=cfg,
                resume_session_id=resume_session_id,
                console=out,
                cli_command_group=cli_command_group,
                finish_banner=finish_banner,
                after_banner=after_banner,
            )
        )
    except (EOFError, KeyboardInterrupt):
        return 0


__all__ = ["run_repl", "run_repl_async"]

"""Interactive REPL for OpenSRE — Claude Code-style incident response terminal."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    import click
    from rich.console import Console

    from config.repl_config import ReplConfig


def run_repl(
    initial_input: str | None = None,
    config: ReplConfig | None = None,
    *,
    resume_session_id: str | None = None,
    console: Console | None = None,
    cli_command_group: click.Command | None = None,
    after_banner: Callable[[], None] | None = None,
    capture_shell_rendered: bool = True,
) -> int:
    """Run the interactive shell and return its exit code.

    Mirrors :func:`surfaces.interactive_shell.main.run_repl`. Importing that
    module pulls in the whole terminal stack, so the import stays inside the
    call — the signature is repeated here rather than erased to ``*args`` so
    callers keep type checking. Repeating it means every runtime parameter has
    to be repeated too: one omitted here is unreachable through this facade,
    which is the seam embedding callers import.
    """
    from surfaces.interactive_shell.main import run_repl as runtime_run_repl

    return runtime_run_repl(
        initial_input=initial_input,
        config=config,
        resume_session_id=resume_session_id,
        console=console,
        cli_command_group=cli_command_group,
        after_banner=after_banner,
        capture_shell_rendered=capture_shell_rendered,
    )


__all__ = ["run_repl"]

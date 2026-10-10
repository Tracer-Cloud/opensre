"""Render terminal output while the agent worker waits and the prompt releases stdin."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.application.current import set_app

from surfaces.interactive_shell.session import Session


def with_exclusive_output[Result](session: Session, render: Callable[[], Result]) -> Result:
    """Suspend a live prompt on its loop, run a local renderer, and return its result."""
    terminal = session.terminal
    app = terminal.prompt_app
    loop = terminal.main_loop
    if terminal.exclusive_stdin_active or app is None or not app.is_running:
        return render()
    if loop is None or not loop.is_running():
        raise RuntimeError("Live prompt has no running event loop")

    def _render() -> Result:
        previous = terminal.exclusive_stdin_active
        terminal.exclusive_stdin_active = True
        try:
            return render()
        finally:
            terminal.exclusive_stdin_active = previous

    async def _run() -> Result:
        with set_app(app):
            return await run_in_terminal(_render, in_executor=False)

    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        current = None
    if current is loop:
        raise RuntimeError("Exclusive output must be requested by the turn worker")
    return asyncio.run_coroutine_threadsafe(_run(), loop).result()


__all__ = ["with_exclusive_output"]

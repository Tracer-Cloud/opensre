"""Prompt-aware stdout proxy that keeps background output above the composer."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TextIO, cast

from prompt_toolkit.application import Application, run_in_terminal
from prompt_toolkit.patch_stdout import StdoutProxy

from surfaces.interactive_shell.ui.input_prompt.synchronized import (
    SYNCED_OUTPUT_END,
    SYNCED_OUTPUT_START,
)


class _AppBoundStdoutProxy(StdoutProxy):
    """Run proxy flushes in the active prompt application's context."""

    def __init__(self, app: Application[str], *, raw: bool) -> None:
        self._target_app = app
        self._redraw_lock = asyncio.Lock()
        super().__init__(raw=raw)

    def _get_app_loop(self) -> asyncio.AbstractEventLoop | None:
        if not self._target_app.is_running:
            return None
        return self._target_app.loop

    def _write_and_flush(
        self,
        loop: asyncio.AbstractEventLoop | None,
        text: str,
    ) -> None:
        def write_and_flush() -> None:
            self._output.enable_autowrap()
            if self.raw:
                self._output.write_raw(text)
            else:
                self._output.write(text)
            self._output.flush()

        async def write_above_prompt() -> None:
            # Terminals that support synchronized output keep the erase, write,
            # and redraw transaction off-screen until the composer is complete.
            # Unsupported terminals safely ignore these private-mode toggles.
            async with self._redraw_lock:
                self._output.write_raw(SYNCED_OUTPUT_START)
                self._output.flush()
                try:
                    await run_in_terminal(write_and_flush, in_executor=False)
                finally:
                    self._output.write_raw(SYNCED_OUTPUT_END)
                    self._output.flush()

        if loop is None:
            write_and_flush()
            return

        def write_in_app_context() -> None:
            context = self._target_app.context
            if context is None or not self._target_app.is_running:
                write_and_flush()
                return
            context.copy().run(
                self._target_app.create_background_task,
                write_above_prompt(),
            )

        loop.call_soon_threadsafe(write_in_app_context)


@contextmanager
def patch_prompt_stdout(app: Application[str], *, raw: bool = False) -> Iterator[None]:
    """Redirect stdout/stderr while preserving the active prompt on redraw."""
    with _AppBoundStdoutProxy(app, raw=raw) as proxy:
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        sys.stdout = cast(TextIO, proxy)
        sys.stderr = cast(TextIO, proxy)
        try:
            yield
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr


__all__ = ["patch_prompt_stdout"]

"""Prompt-aware stdout proxy that keeps background output above the composer."""

from __future__ import annotations

import asyncio
import itertools
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TextIO, cast

from prompt_toolkit.application import Application, run_in_terminal
from prompt_toolkit.patch_stdout import StdoutProxy
from rich.console import RenderableType

from surfaces.interactive_shell.ui.input_prompt.synchronized import synchronized_output
from surfaces.interactive_shell.ui.transcript_view import TranscriptStore, render_text

# In-band markers queued with the text so the flush thread sees them in order
# (prompt-toolkit joins queued items into one string, so they must be text).
# ``write`` strips NUL from captured output, so no output can spell a marker.
_TRANSIENT_START = "\x00opensre-transient-start\x00"
_TRANSIENT_END = "\x00opensre-transient-end\x00"
_RENDERABLE_MARKER = "\x00opensre-renderable-{}\x00"
_MARKER = re.compile(r"\x00opensre-(?:transient-start|transient-end|renderable-(\d+))\x00")


class _AppBoundStdoutProxy(StdoutProxy):
    """Run proxy flushes in the active prompt application's context.

    With a transcript, output is recorded there and the full-screen app redraws
    it; it reaches the terminal directly only while the app is not on screen.
    """

    def __init__(
        self,
        app: Application[str],
        *,
        raw: bool,
        transcript: TranscriptStore | None = None,
    ) -> None:
        self._target_app = app
        self._redraw_lock = asyncio.Lock()
        self._transcript = transcript
        # Read and written only by the flush thread.
        self._transient = False
        self._renderables: dict[int, RenderableType] = {}
        self._renderable_ids = itertools.count()
        # Repaint sooner than the inline default: a full-screen redraw is cheap.
        super().__init__(sleep_between_writes=0.05 if transcript else 0.2, raw=raw)

    def write(self, data: str) -> int:
        """Queue ``data``; NUL is dropped so output cannot forge a marker."""
        if self._transcript is not None and "\x00" in data:
            super().write(data.replace("\x00", ""))
            return len(data)
        return super().write(data)

    def write_renderable(self, renderable: RenderableType) -> bool:
        """Queue a Rich renderable so the transcript can lay it out at any width.

        Returns False when there is no transcript; the caller prints it instead.
        """
        if self._transcript is None:
            return False
        key = next(self._renderable_ids)
        self._renderables[key] = renderable
        self._queue_marker(_RENDERABLE_MARKER.format(key))
        return True

    def begin_transient_output(self) -> None:
        """Paint what follows (an inline menu) without recording it."""
        self._queue_marker(_TRANSIENT_START)

    def end_transient_output(self) -> None:
        """Resume recording output into the transcript."""
        self._queue_marker(_TRANSIENT_END)

    def _queue_marker(self, marker: str) -> None:
        # One lock hold: no other writer's text can land between the flush of
        # earlier output and the marker.
        with self._lock:
            self._flush()
            self._flush_queue.put(marker)

    def _get_app_loop(self) -> asyncio.AbstractEventLoop | None:
        if not self._target_app.is_running:
            return None
        return self._target_app.loop

    def _write_and_flush(
        self,
        loop: asyncio.AbstractEventLoop | None,
        text: str,
    ) -> None:
        if self._transcript is None:
            self._write_to_terminal(loop, text)
            return
        for segment in _split_markers(text):
            if segment == _TRANSIENT_START:
                self._transient = True
            elif segment == _TRANSIENT_END:
                self._transient = False
            elif (match := _MARKER.fullmatch(segment)) is not None:
                self._flush_renderable(loop, self._renderables.pop(int(match.group(1))))
            elif self._transient:
                self._write_to_terminal(loop, segment)
            elif loop is None:
                # The full-screen app is off screen (startup, a picker): the
                # text lands in scrollback now and joins the transcript too.
                self._write_to_terminal(None, segment)
                self._transcript.append_text(segment, on_normal_screen=True)
            else:
                self._transcript.append_text(segment)

    def _flush_renderable(
        self, loop: asyncio.AbstractEventLoop | None, renderable: RenderableType
    ) -> None:
        assert self._transcript is not None
        if loop is not None and not self._transient:
            self._transcript.append_renderable(renderable)
            return
        text = render_text(renderable, self._output.get_size().columns)
        self._write_to_terminal(loop, "\r" + text)
        if not self._transient:
            self._transcript.append_renderable(renderable, on_normal_screen=True)

    def _write_to_terminal(self, loop: asyncio.AbstractEventLoop | None, text: str) -> None:
        def write_and_flush() -> None:
            self._output.enable_autowrap()
            if self.raw:
                self._output.write_raw(text)
            else:
                self._output.write(text)
            self._output.flush()

        async def write_above_prompt() -> None:
            # VT terminals keep the erase, write, and redraw transaction
            # off-screen until the composer is complete. Native Win32 runs
            # the same transaction without DEC private-mode bytes.
            async with self._redraw_lock:
                with synchronized_output(self._output):
                    await run_in_terminal(write_and_flush, in_executor=False)

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


def _split_markers(text: str) -> Iterator[str]:
    """Yield text segments and markers in their original order."""
    start = 0
    for match in _MARKER.finditer(text):
        if match.start() > start:
            yield text[start : match.start()]
        yield match.group(0)
        start = match.end()
    if start < len(text):
        yield text[start:]


@contextmanager
def patch_prompt_stdout(
    app: Application[str],
    *,
    raw: bool = False,
    transcript: TranscriptStore | None = None,
) -> Iterator[None]:
    """Redirect stdout/stderr while preserving the active prompt on redraw."""
    with _AppBoundStdoutProxy(app, raw=raw, transcript=transcript) as proxy:
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

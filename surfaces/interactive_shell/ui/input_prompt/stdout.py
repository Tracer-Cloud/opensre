"""Prompt-aware stdout proxy that keeps background output above the composer."""

from __future__ import annotations

import asyncio
import itertools
import re
import sys
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TextIO, cast

from prompt_toolkit.application import Application, run_in_terminal
from prompt_toolkit.patch_stdout import StdoutProxy
from rich.console import RenderableType

from surfaces.interactive_shell.ui.input_prompt.deferred_output import DeferredOutput
from surfaces.interactive_shell.ui.input_prompt.replay_transcript import ReplayTranscript
from surfaces.interactive_shell.ui.input_prompt.synchronized import synchronized_output
from surfaces.interactive_shell.ui.transcript_view import TranscriptStore, render_text

# In-band markers queued with the text so the flush thread sees them in order
# (prompt-toolkit joins queued items into one string, so they must be text).
# ``write`` strips NUL from captured output, so no output can spell a marker.
_TRANSIENT_START = "\x00opensre-transient-start\x00"
_TRANSIENT_END = "\x00opensre-transient-end\x00"
_DEFERRED_MARKER = "\x00opensre-deferred-{}\x00"
_RENDERABLE_MARKER = "\x00opensre-renderable-{}\x00"
_MARKER = re.compile(r"\x00opensre-(?:transient-(?:start|end)|renderable-(\d+)|deferred-(\d+))\x00")


@dataclass
class _TerminalReplayQueue:
    """Keep one acknowledged record and ordered disk captures on the app loop."""

    on_close: Callable[[DeferredOutput], None]
    captures: deque[DeferredOutput] = field(default_factory=deque)
    current: DeferredOutput | None = None
    records: Iterator[tuple[bool, str]] | None = None
    pending: str | None = None

    def next_output(self) -> str | None:
        if self.pending is not None:
            return self.pending
        while True:
            if self.current is None:
                if not self.captures:
                    return None
                self.current = self.captures.popleft()
                self.records = iter(self.current)
            assert self.records is not None
            try:
                starts_block, text = next(self.records)
            except StopIteration:
                self._close_current()
                continue
            self.pending = "\r" + text if starts_block else text
            return self.pending

    def _close_current(self) -> None:
        captured = self.current
        self.current = None
        self.records = None
        self.pending = None
        if captured is not None:
            self._close_capture(captured)

    def _close_capture(self, captured: DeferredOutput) -> None:
        self.on_close(captured)

    def close(self) -> None:
        """Close all captures even when an earlier file cannot be closed."""
        error: OSError | None = None
        try:
            self._close_current()
        except OSError as exc:
            error = exc
        while self.captures:
            try:
                self._close_capture(self.captures.popleft())
            except OSError as exc:
                error = error or exc
        if error is not None:
            raise error


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
        # Transient state is owned by the flush thread; capture state uses _lock.
        self._transient = False
        self._deferred_depth = 0
        self._deferred_output: DeferredOutput | None = None
        self._deferred_files: dict[int, DeferredOutput] = {}
        self._deferred_ids = itertools.count()
        self._flush_error: Exception | None = None
        self._closing = False
        self._terminal_replays = _TerminalReplayQueue(self._terminal_capture_closed)
        self._terminal_replay_count = 0
        self._terminal_text_capture: DeferredOutput | None = None
        self._terminal_replay_loop: asyncio.AbstractEventLoop | None = None
        self._terminal_replay_task: asyncio.Task[None] | None = None
        self._renderables: dict[int, RenderableType] = {}
        self._renderable_ids = itertools.count()
        # Repaint sooner than the inline default: a full-screen redraw is cheap.
        super().__init__(sleep_between_writes=0.05 if transcript else 0.2, raw=raw)

    def write(self, data: str) -> int:
        """Queue ``data``; NUL is dropped so output cannot forge a marker."""
        with self._lock:
            self._ensure_open()
            self._raise_flush_error()
            text = data.replace("\x00", "")
            if self._deferred_output is not None:
                self._deferred_output.write(text)
            else:
                super().write(text)
        return len(data)

    def write_renderable(self, renderable: RenderableType) -> bool:
        """Queue a Rich renderable so the transcript can lay it out at any width.

        Returns False when there is no transcript; the caller prints it instead.
        """
        if self._transcript is None:
            return False
        with self._lock:
            self._ensure_open()
            self._raise_flush_error()
            if self._deferred_output is not None:
                # Snapshot only deferred renderables so the aggregate backlog
                # never retains live objects; normal output still reflows.
                self._deferred_output.write(
                    render_text(renderable, self._output.get_size().columns), block=True
                )
            else:
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

    @contextmanager
    def defer_output(self) -> Iterator[None]:
        """Capture output on disk until the outermost full-screen form closes."""
        with self._lock:
            self._ensure_open()
            self._raise_flush_error()
            if not self._deferred_depth:
                captured = DeferredOutput()
                self._flush()
                self._deferred_output = captured
            self._deferred_depth += 1
        try:
            yield
        finally:
            with self._lock:
                self._deferred_depth -= 1
                if not self._deferred_depth:
                    self._seal_deferred_output()

    def _seal_deferred_output(self) -> None:
        captured = self._deferred_output
        if captured is not None:
            self._deferred_output = None
            key = next(self._deferred_ids)
            self._deferred_files[key] = captured
            self._queue_marker(_DEFERRED_MARKER.format(key))

    def close(self) -> None:
        if not self.closed:
            with self._lock:
                self._closing = True
                self._seal_deferred_output()
                self._flush()
            try:
                super().close()
            finally:
                for captured in self._deferred_files.values():
                    captured.close()
                self._deferred_files.clear()
        self._raise_flush_error()

    def __exit__(self, *args: object) -> None:
        try:
            self.close()
        except OSError as error:
            original = args[1] if len(args) > 1 else None
            if not isinstance(original, BaseException):
                raise
            original.add_note(str(error))

    def _raise_flush_error(self) -> None:
        if self._flush_error is not None:
            raise OSError("Deferred output could not be replayed") from self._flush_error

    def _ensure_open(self) -> None:
        if self.closed or self._closing:
            raise ValueError("I/O operation on closed stdout proxy")

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
        for segment in _split_markers(text):
            match = _MARKER.fullmatch(segment)
            if match is not None and match.group(2) is not None:
                self._replay_deferred_output(loop, int(match.group(2)))
            elif segment == _TRANSIENT_START:
                self._transient = True
            elif segment == _TRANSIENT_END:
                self._transient = False
            elif match is not None:
                self._flush_renderable(loop, self._renderables.pop(int(match.group(1))))
            else:
                self._emit_text(loop, segment)

    def _replay_deferred_output(self, loop: asyncio.AbstractEventLoop | None, key: int) -> None:
        captured = self._deferred_files.pop(key)
        replay_loop = loop or self._terminal_replay_loop
        if replay_loop is not None and replay_loop.is_running() and self._transcript is None:
            self._schedule_terminal_replay(replay_loop, captured)
            return
        try:
            transcript = ReplayTranscript()
            for starts_block, text in captured:
                if starts_block:
                    if final := transcript.finish():
                        self._emit_text(loop, "", final)
                    if self._transcript is not None:
                        self._transcript.close_pending_line()
                    if loop is None or self._transient:
                        self._write_to_terminal(loop, "\r")
                    transcript = ReplayTranscript()
                self._emit_text(loop, text, transcript.feed(text))
            if final := transcript.finish():
                self._emit_text(loop, "", final)
        except Exception as exc:
            self._flush_error = exc
        finally:
            try:
                captured.close()
            except OSError as exc:
                self._flush_error = exc

    def _schedule_terminal_replay(
        self, loop: asyncio.AbstractEventLoop, captured: DeferredOutput, *, ordinary: bool = False
    ) -> None:
        with self._lock:
            if not ordinary:
                self._terminal_text_capture = None
            self._terminal_replay_loop = loop
            # Count before scheduling: later text must also join the FIFO while
            # its first enqueue callback has not had a chance to run yet.
            self._terminal_replay_count += 1

        def enqueue() -> None:
            self._terminal_replays.captures.append(captured)
            if self._terminal_replay_task is None:
                self._start_terminal_replay(loop)

        try:
            loop.call_soon_threadsafe(enqueue)
        except Exception as exc:
            self._flush_error = exc
            try:
                self._terminal_capture_closed(captured)
            except OSError as close_error:
                self._flush_error = close_error

    def _terminal_capture_closed(self, captured: DeferredOutput) -> None:
        with self._lock:
            if self._terminal_text_capture is captured:
                self._terminal_text_capture = None
            try:
                captured.close()
            finally:
                self._terminal_replay_count -= 1

    def _queue_text_behind_terminal_replay(self, text: str) -> bool:
        with self._lock:
            if not self._terminal_replay_count:
                return False
            loop = self._terminal_replay_loop
            assert loop is not None
            captured = self._terminal_text_capture
            created = captured is None
            try:
                if captured is None:
                    captured = DeferredOutput()
                captured.write(text)
            except Exception as exc:
                self._flush_error = exc
                self._terminal_text_capture = None
                if created and captured is not None:
                    try:
                        captured.close()
                    except OSError as close_error:
                        self._flush_error = close_error
            else:
                if created:
                    self._terminal_text_capture = captured
                    self._schedule_terminal_replay(loop, captured, ordinary=True)
            return True

    def _next_terminal_output(self) -> str | None:
        with self._lock:
            return self._terminal_replays.next_output()

    def _write_pending_terminal_record(self) -> None:
        pending = self._terminal_replays.pending
        if pending is not None:
            self._write_to_terminal(None, pending)
            self._terminal_replays.pending = None

    def _close_terminal_replays(self) -> None:
        try:
            with self._lock:
                self._terminal_replays.close()
        except OSError as exc:
            self._flush_error = exc
            raise

    def _drain_terminal_replays(self) -> None:
        try:
            while self._next_terminal_output() is not None:
                self._write_pending_terminal_record()
        except Exception as exc:
            self._flush_error = exc
            raise
        finally:
            self._close_terminal_replays()

    def _start_terminal_replay(self, loop: asyncio.AbstractEventLoop) -> None:
        if (
            self._target_app.context is None
            or not self._target_app.is_running
            or self._target_app.is_done
        ):
            self._drain_terminal_replays()
            return
        started = False

        async def replay() -> None:
            nonlocal started
            started = True
            try:
                # One worker owns every capture, including shutdown fallback.
                # Read another bounded record only after acknowledging this one.
                async with self._redraw_lock:
                    while self._next_terminal_output() is not None:
                        if self._target_app.context is None or not self._target_app.is_running:
                            self._write_pending_terminal_record()
                            await asyncio.sleep(0)
                        else:
                            with synchronized_output(self._output):
                                await run_in_terminal(
                                    self._write_pending_terminal_record, in_executor=False
                                )
            except asyncio.CancelledError:
                self._drain_terminal_replays()
                raise
            except Exception as exc:
                self._flush_error = exc
                raise
            finally:
                self._close_terminal_replays()

        def finished(task: asyncio.Task[None]) -> None:
            if self._terminal_replay_task is not task:
                return
            try:
                # An unstarted task never enters its finally; captures can also
                # arrive between cancellation cleanup and this done callback.
                if not started or task.cancelled():
                    self._drain_terminal_replays()
            except Exception as exc:
                self._flush_error = exc
                loop.call_exception_handler(
                    {"message": "Deferred terminal replay failed", "exception": exc, "task": task}
                )
            finally:
                self._terminal_replay_task = None
            if self._terminal_replays.captures:
                self._start_terminal_replay(loop)

        context = self._target_app.context
        assert context is not None
        task = context.copy().run(self._target_app.create_background_task, replay())
        self._terminal_replay_task = task
        task.add_done_callback(finished)

    def _emit_text(
        self, loop: asyncio.AbstractEventLoop | None, text: str, transcript_text: str | None = None
    ) -> None:
        recorded = text if transcript_text is None else transcript_text
        if self._transcript is None and self._queue_text_behind_terminal_replay(text):
            return
        if self._transcript is None or self._transient:
            self._write_to_terminal(loop, text)
        elif loop is None:
            # The full-screen app is off screen (startup, a picker): the
            # text lands in scrollback now and joins the transcript too.
            self._write_to_terminal(None, text)
            self._transcript.append_text(recorded, on_normal_screen=True)
        else:
            self._transcript.append_text(recorded)

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

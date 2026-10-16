"""Deferred output stays off-screen without retaining the whole backlog in RAM."""

from __future__ import annotations

import asyncio
import contextvars
import gc
import hashlib
import io
import threading
import tracemalloc
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, BinaryIO, cast

import pytest
from prompt_toolkit.application import Application, create_app_session
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.base import Size
from rich.text import Text

import surfaces.interactive_shell.ui.input_prompt.deferred_output as deferred
import surfaces.interactive_shell.ui.input_prompt.stdout as stdout_module
from surfaces.interactive_shell.ui.input_prompt.stdout import _AppBoundStdoutProxy
from surfaces.interactive_shell.ui.transcript_view import TranscriptStore


class _Output(DummyOutput):
    def __init__(self) -> None:
        self.bytes_written = 0
        self.digest = hashlib.sha256()
        self.largest_write = 0
        self.text = io.StringIO()
        self.retain_text = True

    def write(self, data: str) -> None:
        self.write_raw(data)

    def write_raw(self, data: str) -> None:
        encoded = data.encode("utf-8")
        self.digest.update(encoded)
        self.bytes_written += len(encoded)
        self.largest_write = max(self.largest_write, len(data))
        if self.retain_text:
            self.text.write(data)

    def get_size(self) -> Size:
        return Size(rows=24, columns=80)


class _Alert:
    def __init__(self, text: str) -> None:
        self.text = text

    def __rich__(self) -> Text:
        return Text(self.text)


class _ActiveApp:
    is_running = True
    is_done = False

    def __init__(self, tasks: list[asyncio.Task[None]]) -> None:
        self.loop = asyncio.get_running_loop()
        self.context = contextvars.copy_context()
        self.tasks = tasks

    def create_background_task(self, coroutine: Any) -> asyncio.Task[None]:
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task


@contextmanager
def _proxy(store: TranscriptStore | None = None) -> Iterator[tuple[_AppBoundStdoutProxy, _Output]]:
    output = _Output()
    with create_app_session(input=DummyInput(), output=output):
        app = Application[str](input=DummyInput(), output=output)
        proxy = _AppBoundStdoutProxy(app, raw=True, transcript=store)
        try:
            yield proxy, output
        finally:
            if not proxy.closed:
                proxy.close()


def test_long_form_capture_stays_bounded_and_replays_in_small_writes() -> None:
    expected = hashlib.sha256()
    with _proxy() as (proxy, output):
        output.retain_text = False
        with proxy.defer_output():
            tracemalloc.start()
            try:
                for index in range(4096):
                    # No newline: neither the proxy's line buffer nor its queue may accumulate it.
                    chunk = f"{index:08d}" + "x" * 4088
                    expected.update(chunk.encode())
                    proxy.write(chunk)
                gc.collect()
                retained, _peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            assert output.bytes_written == 0
            assert retained < 2 * 1024 * 1024
        proxy.close()

    assert output.bytes_written == 16 * 1024 * 1024
    assert output.digest.digest() == expected.digest()
    assert output.largest_write <= 64 * 1024


def test_deferred_rich_objects_are_released_before_the_form_closes() -> None:
    store = TranscriptStore()
    with _proxy(store) as (proxy, output), proxy.defer_output():
        references: list[weakref.ReferenceType[_Alert]] = []
        for index in range(128):
            item = _Alert(f"alert {index}")
            references.append(weakref.ref(item))
            assert proxy.write_renderable(item)
        del item
        gc.collect()
        assert all(reference() is None for reference in references)
        assert output.bytes_written == 0

    text = output.text.getvalue()
    assert text.index("alert 0") < text.index("alert 127")
    rows, _older = store.tail_rows(80, 256)
    assert any("alert 127" in "".join(part for _style, part in row) for row in rows)


def test_nested_forms_preserve_text_and_rich_order() -> None:
    store = TranscriptStore()
    with _proxy(store) as (proxy, output):
        proxy.write("before\n")
        with proxy.defer_output():
            proxy.write("outer\n")
            with proxy.defer_output():
                assert proxy.write_renderable(Text("inner alert"))
            proxy.write("last\n")
        proxy.write("after\n")
        proxy.close()

    text = output.text.getvalue()
    positions = [text.index(value) for value in ("before", "outer", "inner alert", "last", "after")]
    assert positions == sorted(positions)
    rows, _older = store.tail_rows(80, 20)
    transcript = "\n".join("".join(part for _style, part in row) for row in rows)
    assert "inner alert" in transcript


def test_form_exception_still_releases_accepted_output() -> None:
    with _proxy() as (proxy, output):
        with pytest.raises(RuntimeError, match="form failed"), proxy.defer_output():
            proxy.write("accepted alert\n")
            raise RuntimeError("form failed")
        proxy.close()
    assert output.text.getvalue() == "accepted alert\n"


def test_close_inside_a_form_flushes_once_and_outer_exit_is_safe() -> None:
    with _proxy() as (proxy, output):
        with proxy.defer_output():
            proxy.write("accepted without newline")
            proxy.close()
        proxy.close()
    assert output.text.getvalue() == "accepted without newline"


def test_deferred_output_cannot_forge_internal_markers() -> None:
    with _proxy() as (proxy, output), proxy.defer_output():
        proxy.write("\x00opensre-deferred-123\x00\n")
    assert output.text.getvalue() == "opensre-deferred-123\n"


def test_partial_disk_write_preserves_the_previously_accepted_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = deferred.DeferredOutput()
    captured.write("accepted alert\n")
    write = captured._file.write
    calls = 0

    def failing_write(data: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 2:
            write(data[:2])
            raise OSError("disk full")
        return write(data)

    try:
        monkeypatch.setattr(captured._file, "write", failing_write)
        with pytest.raises(OSError, match="disk full"):
            captured.write("unaccepted output")
        assert list(captured) == [(False, "accepted alert\n")]
    finally:
        captured.close()


@pytest.fixture
def spool_files(monkeypatch: pytest.MonkeyPatch) -> list[BinaryIO]:
    opened: list[BinaryIO] = []
    create = deferred.tempfile.TemporaryFile

    def tracked_file(*, mode: str, buffering: int = -1) -> BinaryIO:
        handle = cast(BinaryIO, create(mode=mode, buffering=buffering))
        opened.append(handle)
        return handle

    monkeypatch.setattr(deferred.tempfile, "TemporaryFile", tracked_file)
    return opened


@pytest.mark.parametrize("cancel", [False, True])
def test_aborted_forms_close_their_disk_spool(spool_files: list[BinaryIO], cancel: bool) -> None:
    error = asyncio.CancelledError if cancel else RuntimeError
    with _proxy() as (proxy, output):
        with pytest.raises(error), proxy.defer_output():
            proxy.write("accepted alert\n")
            raise error()
        proxy.close()
    assert output.text.getvalue() == "accepted alert\n"
    assert spool_files and all(handle.closed for handle in spool_files)


def test_replay_errors_are_reported_and_do_not_leak_files(
    monkeypatch: pytest.MonkeyPatch, spool_files: list[BinaryIO]
) -> None:
    def failing_read(_self: deferred.DeferredOutput) -> Iterator[str]:
        raise OSError("disk read failed")

    monkeypatch.setattr(deferred.DeferredOutput, "__iter__", failing_read)
    with _proxy() as (proxy, _output):
        with proxy.defer_output():
            proxy.write("alert\n")
        with pytest.raises(OSError, match="could not be replayed"):
            proxy.close()
    assert spool_files and all(handle.closed for handle in spool_files)


def test_replay_failure_does_not_hide_the_original_form_exception(
    monkeypatch: pytest.MonkeyPatch, spool_files: list[BinaryIO]
) -> None:
    def failing_read(_self: deferred.DeferredOutput) -> Iterator[str]:
        raise OSError("disk read failed")

    monkeypatch.setattr(deferred.DeferredOutput, "__iter__", failing_read)
    with (
        _proxy() as (proxy, _output),
        pytest.raises(RuntimeError, match="form failed") as caught,
        proxy,
        proxy.defer_output(),
    ):
        proxy.write("alert\n")
        raise RuntimeError("form failed")
    assert any("could not be replayed" in note for note in caught.value.__notes__)
    assert spool_files and all(handle.closed for handle in spool_files)


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        (["prefix\x1b[", "31mALERT\x1b[0m\n"], "prefixALERT"),
        (["kept\r", "\n"], "kept"),
        (["kept\x1b]8;;https://example.test", "\x1b", "\\ALERT\n"], "keptALERT"),
        (["obsolete\n\x1b[", "2Jfresh\n"], "fresh"),
    ],
    ids=["split-sgr", "split-crlf", "split-osc", "split-clear"],
)
def test_replay_preserves_terminal_sequences_across_capture_records(
    chunks: list[str], expected: str
) -> None:
    store = TranscriptStore()
    with _proxy(store) as (proxy, output), proxy.defer_output():
        for chunk in chunks:
            proxy.write(chunk)
    assert output.text.getvalue() == "".join(chunks)
    rows, _older = store.tail_rows(80, 20)
    assert "\n".join("".join(text for _style, text in row) for row in rows) == expected


def test_replay_carries_colour_to_the_next_record_and_honours_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    store = TranscriptStore()
    with _proxy(store) as (proxy, _output), proxy.defer_output():
        proxy.write("\x1b[31mfirst\n")
        proxy.write("second\n")
        proxy.write("\x1b[0mplain\n")
    rows, _older = store.tail_rows(80, 20)
    coloured = ["".join(text for style, text in row if "red" in style) for row in rows]
    assert coloured == ["first", "second", ""]


def test_closed_proxy_rejects_output_instead_of_accepting_an_unread_queue() -> None:
    with _proxy() as (proxy, output):
        proxy.close()
        with pytest.raises(ValueError, match="closed"):
            proxy.write("lost alert")
    assert output.bytes_written == 0


@pytest.mark.parametrize(
    ("chunks", "red_rows"),
    [
        (["\x1b[31mfirst\npartial", "tail\n"], ["first", "partialtail"]),
        (["\x1b[31mfirst\npartial", "\x1b[0mtail\n"], ["first", "partial"]),
        (["\x1b[31mold\rpartial", "tail\n"], ["partialtail"]),
    ],
    ids=["newline-carry", "newline-reset", "carriage-return"],
)
def test_partial_lines_keep_the_style_active_at_their_start(
    monkeypatch: pytest.MonkeyPatch, chunks: list[str], red_rows: list[str]
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    store = TranscriptStore()
    with _proxy(store) as (proxy, _output), proxy.defer_output():
        for chunk in chunks:
            proxy.write(chunk)
    rows, _older = store.tail_rows(80, 20)
    assert ["".join(text for style, text in row if "red" in style) for row in rows] == red_rows


def test_rich_alert_starts_a_block_after_an_unfinished_text_line() -> None:
    store = TranscriptStore()
    with _proxy(store) as (proxy, _output), proxy.defer_output():
        proxy.write("partial")
        proxy.write_renderable(Text("alert"))
    rows, _older = store.tail_rows(80, 20)
    assert ["".join(text for _style, text in row) for row in rows] == ["partial", "alert"]


@pytest.mark.asyncio
async def test_busy_terminal_replay_does_not_queue_the_entire_disk_backlog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []

    async def slow_terminal(function: Any, *, in_executor: bool) -> None:
        entered.set()
        await release.wait()
        function()

    monkeypatch.setattr(stdout_module, "run_in_terminal", slow_terminal)
    with _proxy() as (proxy, output):
        output.retain_text = False
        proxy._target_app = cast(Application[str], _ActiveApp(tasks))
        with proxy.defer_output():
            proxy.write("x" * (4 * 1024 * 1024))
        tracemalloc.start()
        try:
            proxy.close()
            await asyncio.wait_for(entered.wait(), timeout=5)
            await asyncio.sleep(0)
            retained, _peak = tracemalloc.get_traced_memory()
            assert retained < 2 * 1024 * 1024
        finally:
            tracemalloc.stop()
            release.set()
            await asyncio.gather(*tasks)
    assert output.bytes_written == 4 * 1024 * 1024


@pytest.mark.asyncio
@pytest.mark.parametrize("close_failure", [False, True])
async def test_async_replay_reports_disk_failure_to_the_application_task(
    monkeypatch: pytest.MonkeyPatch, spool_files: list[BinaryIO], close_failure: bool
) -> None:
    tasks: list[asyncio.Task[None]] = []
    close = deferred.DeferredOutput.close

    def failing_read(_self: deferred.DeferredOutput) -> Iterator[tuple[bool, str]]:
        raise OSError("disk read failed")

    def failing_close(captured: deferred.DeferredOutput) -> None:
        close(captured)
        raise OSError("disk close failed")

    if close_failure:
        monkeypatch.setattr(deferred.DeferredOutput, "close", failing_close)
    else:
        monkeypatch.setattr(deferred.DeferredOutput, "__iter__", failing_read)
    with _proxy() as (proxy, _output):
        proxy._target_app = cast(Application[str], _ActiveApp(tasks))
        with proxy.defer_output():
            proxy.write("alert\n")
        with proxy.defer_output():
            proxy.write("later alert\n")
        proxy.close()
        await asyncio.sleep(0)
        with pytest.raises(
            OSError, match="disk close failed" if close_failure else "disk read failed"
        ):
            await tasks[0]
        assert proxy._terminal_replay_count == 0
    assert spool_files and all(handle.closed for handle in spool_files)


@pytest.mark.asyncio
@pytest.mark.parametrize("current_written", [False, True])
async def test_cancelled_terminal_replay_drains_each_accepted_record_once(
    monkeypatch: pytest.MonkeyPatch, spool_files: list[BinaryIO], current_written: bool
) -> None:
    entered = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []

    async def interrupted_terminal(function: Any, *, in_executor: bool) -> None:
        if current_written:
            function()
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(stdout_module, "run_in_terminal", interrupted_terminal)
    with _proxy() as (proxy, output):
        proxy._target_app = cast(Application[str], _ActiveApp(tasks))
        with proxy.defer_output():
            proxy.write("first alert\n")
            proxy.write("second alert\n")
        proxy.close()
        await asyncio.wait_for(entered.wait(), timeout=5)
        tasks[0].cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert proxy._flush_error is None
    assert output.text.getvalue() == "first alert\nsecond alert\n"
    assert spool_files and all(handle.closed for handle in spool_files)


@pytest.mark.asyncio
async def test_replay_cancelled_before_its_first_step_still_drains_and_closes(
    spool_files: list[BinaryIO],
) -> None:
    tasks: list[asyncio.Task[None]] = []

    class CancelBeforeStartApp(_ActiveApp):
        def create_background_task(self, coroutine: Any) -> asyncio.Task[None]:
            task = super().create_background_task(coroutine)
            task.cancel()
            return task

    with _proxy() as (proxy, output):
        proxy._target_app = cast(Application[str], CancelBeforeStartApp(tasks))
        with proxy.defer_output():
            proxy.write("accepted alert\n")
        with proxy.defer_output():
            proxy.write("later alert\n")
        proxy.close()
        await asyncio.sleep(0)
        await asyncio.gather(*tasks, return_exceptions=True)
        assert proxy._flush_error is None
        assert len(tasks) == 1
    assert output.text.getvalue() == "accepted alert\nlater alert\n"
    assert spool_files and all(handle.closed for handle in spool_files)


@pytest.mark.asyncio
async def test_reverse_cancellation_keeps_multiple_captures_in_fifo_order(
    monkeypatch: pytest.MonkeyPatch, spool_files: list[BinaryIO]
) -> None:
    entered = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []

    async def interrupted_terminal(function: Any, *, in_executor: bool) -> None:
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(stdout_module, "run_in_terminal", interrupted_terminal)
    with _proxy() as (proxy, output):
        proxy._target_app = cast(Application[str], _ActiveApp(tasks))
        with proxy.defer_output():
            proxy.write("first capture\n")
            proxy.write("first capture tail\n")
        with proxy.defer_output():
            proxy.write("second capture\n")
        proxy.close()
        await asyncio.wait_for(entered.wait(), timeout=5)
        await asyncio.sleep(0)
        for task in reversed(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert proxy._flush_error is None
    assert output.text.getvalue() == "first capture\nfirst capture tail\nsecond capture\n"
    assert spool_files and all(handle.closed for handle in spool_files)


@pytest.mark.asyncio
@pytest.mark.parametrize("app_running", [True, False])
async def test_later_captures_wait_for_the_same_terminal_worker(
    monkeypatch: pytest.MonkeyPatch, spool_files: list[BinaryIO], app_running: bool
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []

    async def slow_terminal(function: Any, *, in_executor: bool) -> None:
        entered.set()
        await release.wait()
        function()

    monkeypatch.setattr(stdout_module, "run_in_terminal", slow_terminal)
    with _proxy() as (proxy, output):
        app = _ActiveApp(tasks)
        proxy._target_app = cast(Application[str], app)
        with proxy.defer_output():
            proxy.write("first capture\n")
        await asyncio.wait_for(entered.wait(), timeout=5)
        app.is_running = app_running
        with proxy.defer_output():
            proxy.write("second capture\n")
        proxy.close()
        await asyncio.sleep(0)
        assert output.text.getvalue() == ""
        assert len(tasks) == 1
        release.set()
        await asyncio.gather(*tasks)
        assert proxy._flush_error is None
    assert output.text.getvalue() == "first capture\nsecond capture\n"
    assert spool_files and all(handle.closed for handle in spool_files)


@pytest.mark.asyncio
async def test_capture_arriving_after_cancellation_joins_the_same_fifo_cleanup(
    monkeypatch: pytest.MonkeyPatch, spool_files: list[BinaryIO]
) -> None:
    entered = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []

    async def interrupted_terminal(function: Any, *, in_executor: bool) -> None:
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(stdout_module, "run_in_terminal", interrupted_terminal)
    with _proxy() as (proxy, output):
        proxy._target_app = cast(Application[str], _ActiveApp(tasks))
        with proxy.defer_output():
            proxy.write("first capture\n")
        await asyncio.wait_for(entered.wait(), timeout=5)
        tasks[0].cancel()
        with proxy.defer_output():
            proxy.write("later capture\n")
        proxy.close()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert len(tasks) == 1
        assert proxy._flush_error is None
    assert output.text.getvalue() == "first capture\nlater capture\n"
    assert spool_files and all(handle.closed for handle in spool_files)


@pytest.mark.asyncio
async def test_late_capture_after_app_result_does_not_start_an_unmanaged_task(
    monkeypatch: pytest.MonkeyPatch, spool_files: list[BinaryIO]
) -> None:
    entered = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []

    async def immediate_terminal(function: Any, *, in_executor: bool) -> None:
        function()
        entered.set()

    monkeypatch.setattr(stdout_module, "run_in_terminal", immediate_terminal)
    with _proxy() as (proxy, output):
        app = _ActiveApp(tasks)
        proxy._target_app = cast(Application[str], app)
        with proxy.defer_output():
            proxy.write("first capture\n")
        await asyncio.wait_for(entered.wait(), timeout=5)
        await asyncio.gather(*tasks)
        # prompt_toolkit keeps is_running true while cancelling background tasks.
        app.is_done = True
        with proxy.defer_output():
            proxy.write("shutdown capture\n")
        proxy.close()
        await asyncio.sleep(0)
        assert len(tasks) == 1
        assert proxy._flush_error is None
    assert output.text.getvalue() == "first capture\nshutdown capture\n"
    assert spool_files and all(handle.closed for handle in spool_files)


@pytest.mark.asyncio
@pytest.mark.parametrize("app_running", [True, False])
async def test_plain_output_between_captures_keeps_its_fifo_position(
    monkeypatch: pytest.MonkeyPatch, spool_files: list[BinaryIO], app_running: bool
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []

    async def slow_terminal(function: Any, *, in_executor: bool) -> None:
        entered.set()
        await release.wait()
        function()

    monkeypatch.setattr(stdout_module, "run_in_terminal", slow_terminal)
    with _proxy() as (proxy, output):
        app = _ActiveApp(tasks)
        proxy._target_app = cast(Application[str], app)
        with proxy.defer_output():
            proxy.write("capture A\n")
        if not app_running:
            await asyncio.wait_for(entered.wait(), timeout=5)
            app.is_running = False
        # In the running case no enqueue callback has executed yet.
        proxy.write("plain B\n")
        with proxy.defer_output():
            proxy.write("capture C\n")
        proxy.close()
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert output.text.getvalue() == ""
        release.set()
        await asyncio.gather(*tasks)
        assert proxy._flush_error is None
    assert output.text.getvalue() == "capture A\nplain B\ncapture C\n"
    assert spool_files and all(handle.closed for handle in spool_files)


@pytest.mark.asyncio
async def test_thousands_of_waiting_plain_flushes_share_one_disk_spool(
    monkeypatch: pytest.MonkeyPatch, spool_files: list[BinaryIO]
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []

    async def slow_terminal(function: Any, *, in_executor: bool) -> None:
        entered.set()
        await release.wait()
        function()

    monkeypatch.setattr(stdout_module, "run_in_terminal", slow_terminal)
    with _proxy() as (proxy, output):
        app = _ActiveApp(tasks)
        proxy._target_app = cast(Application[str], app)
        with proxy.defer_output():
            proxy.write("capture A\n")
        await asyncio.wait_for(entered.wait(), timeout=5)
        try:
            for index in range(2048):
                # Exercise separate production flushes without queue batching.
                proxy._write_and_flush(app.loop, f"plain {index}\n")
            opened_while_blocked = sum(not handle.closed for handle in spool_files)
        finally:
            try:
                proxy.close()
            finally:
                release.set()
                await asyncio.sleep(0)
                await asyncio.gather(*tasks)
    assert opened_while_blocked <= 2
    assert output.text.getvalue() == "capture A\n" + "".join(
        f"plain {index}\n" for index in range(2048)
    )
    assert spool_files and all(handle.closed for handle in spool_files)


@pytest.mark.asyncio
async def test_plain_spool_does_not_extend_past_the_next_form_capture(
    monkeypatch: pytest.MonkeyPatch, spool_files: list[BinaryIO]
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []

    async def slow_terminal(function: Any, *, in_executor: bool) -> None:
        entered.set()
        await release.wait()
        function()

    monkeypatch.setattr(stdout_module, "run_in_terminal", slow_terminal)
    with _proxy() as (proxy, output):
        app = _ActiveApp(tasks)
        proxy._target_app = cast(Application[str], app)
        with proxy.defer_output():
            proxy.write("capture A\n")
        await asyncio.wait_for(entered.wait(), timeout=5)
        proxy._write_and_flush(app.loop, "plain B\n")
        with proxy.defer_output():
            proxy.write("capture C\n")
        proxy.write("plain D\n")
        proxy.close()
        release.set()
        await asyncio.sleep(0)
        await asyncio.gather(*tasks)
    assert output.text.getvalue() == "capture A\nplain B\ncapture C\nplain D\n"
    assert spool_files and all(handle.closed for handle in spool_files)


@pytest.mark.asyncio
async def test_append_at_plain_spool_eof_is_not_written_to_a_closing_file(
    monkeypatch: pytest.MonkeyPatch, spool_files: list[BinaryIO]
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    eof = threading.Event()
    attempted = threading.Event()
    appended = threading.Event()
    errors: list[Exception] = []
    tasks: list[asyncio.Task[None]] = []

    async def slow_terminal(function: Any, *, in_executor: bool) -> None:
        entered.set()
        await release.wait()
        function()

    monkeypatch.setattr(stdout_module, "run_in_terminal", slow_terminal)
    with _proxy() as (proxy, output):
        app = _ActiveApp(tasks)
        proxy._target_app = cast(Application[str], app)
        with proxy.defer_output():
            proxy.write("capture A\n")
        await asyncio.wait_for(entered.wait(), timeout=5)
        proxy._write_and_flush(app.loop, "plain B\n")
        read = spool_files[1].read

        def observe_eof(size: int) -> bytes:
            data = read(size)
            if size == 4 and not data:
                eof.set()
                assert attempted.wait(timeout=5)
                # Let a racing writer try the append while EOF is being closed.
                appended.wait(timeout=0.1)
            return data

        def append_at_eof() -> None:
            try:
                if not eof.wait(timeout=5):
                    raise TimeoutError("Reader did not reach EOF")
                attempted.set()
                proxy._write_and_flush(app.loop, "late tail\n")
            except Exception as exc:
                errors.append(exc)
            finally:
                appended.set()

        monkeypatch.setattr(spool_files[1], "read", observe_eof)
        writer = threading.Thread(target=append_at_eof)
        writer.start()
        try:
            release.set()
            assert await asyncio.to_thread(appended.wait, 5)
            await asyncio.sleep(0)
            proxy.close()
            await asyncio.gather(*tasks)
        finally:
            await asyncio.to_thread(writer.join, 5)
        assert not errors
        assert proxy._flush_error is None
    assert output.text.getvalue() == "capture A\nplain B\nlate tail\n"
    assert spool_files and all(handle.closed for handle in spool_files)


@pytest.mark.asyncio
async def test_failed_plain_append_keeps_accepted_records_available_for_replay(
    monkeypatch: pytest.MonkeyPatch, spool_files: list[BinaryIO]
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []

    async def slow_terminal(function: Any, *, in_executor: bool) -> None:
        entered.set()
        await release.wait()
        function()

    monkeypatch.setattr(stdout_module, "run_in_terminal", slow_terminal)
    with _proxy() as (proxy, output):
        app = _ActiveApp(tasks)
        proxy._target_app = cast(Application[str], app)
        with proxy.defer_output():
            proxy.write("capture A\n")
        await asyncio.wait_for(entered.wait(), timeout=5)
        proxy._write_and_flush(app.loop, "accepted B\n")

        def failed_write(_data: bytes) -> int:
            raise OSError("disk full")

        monkeypatch.setattr(spool_files[1], "write", failed_write)
        proxy._write_and_flush(app.loop, "unaccepted C\n")
        assert not spool_files[1].closed
        try:
            with pytest.raises(OSError, match="could not be replayed"):
                proxy.close()
        finally:
            release.set()
            await asyncio.sleep(0)
            await asyncio.gather(*tasks)
        assert proxy._terminal_replay_count == 0
    assert output.text.getvalue() == "capture A\naccepted B\n"
    assert spool_files and all(handle.closed for handle in spool_files)


@pytest.mark.asyncio
async def test_plain_spool_accepts_more_records_while_its_reader_is_waiting(
    monkeypatch: pytest.MonkeyPatch, spool_files: list[BinaryIO]
) -> None:
    entered_a = asyncio.Event()
    release_a = asyncio.Event()
    entered_b = asyncio.Event()
    release_b = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []
    calls = 0

    async def paused_terminal(function: Any, *, in_executor: bool) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            entered_a.set()
            await release_a.wait()
        elif calls == 2:
            entered_b.set()
            await release_b.wait()
        function()

    monkeypatch.setattr(stdout_module, "run_in_terminal", paused_terminal)
    with _proxy() as (proxy, output):
        app = _ActiveApp(tasks)
        proxy._target_app = cast(Application[str], app)
        with proxy.defer_output():
            proxy.write("capture A\n")
        await asyncio.wait_for(entered_a.wait(), timeout=5)
        proxy._write_and_flush(app.loop, "plain first\n")
        proxy._write_and_flush(app.loop, "plain unread\n")
        release_a.set()
        await asyncio.wait_for(entered_b.wait(), timeout=5)
        proxy._write_and_flush(app.loop, "plain appended\n")
        proxy.close()
        release_b.set()
        await asyncio.gather(*tasks)
    assert output.text.getvalue() == "capture A\nplain first\nplain unread\nplain appended\n"
    assert len(spool_files) == 2
    assert all(handle.closed for handle in spool_files)

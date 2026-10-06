"""Full-screen transcript: re-layout per width, recording rules, scrollback catch-up."""

from __future__ import annotations

import asyncio
import io
import sys
import threading

import pytest
from prompt_toolkit.output.base import Size
from prompt_toolkit.output.vt100 import Vt100_Output
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from surfaces.interactive_shell.ui.input_prompt.stdout import _TRANSIENT_END, _AppBoundStdoutProxy
from surfaces.interactive_shell.ui.streaming.console import StreamingConsole
from surfaces.interactive_shell.ui.transcript_view import TranscriptControl, TranscriptStore
from surfaces.interactive_shell.ui.transcript_view.store import render_rows, trim_row_padding
from surfaces.shared.terminal.banner import ResponsiveLaunchBanner
from surfaces.shared.terminal.components.rendering import record_in_transcript

_PARAGRAPH = "OpenSRE found three failing jobs and the flaky one burned two hours of runner time"


def _plain_rows(store: TranscriptStore, *, width: int, count: int = 50) -> list[str]:
    rows, _older = store.tail_rows(width, count)
    return ["".join(text for _style, text in row).rstrip() for row in rows]


def test_renderables_are_laid_out_again_at_each_width() -> None:
    store = TranscriptStore()
    store.append_renderable(Markdown(_PARAGRAPH))

    narrow = _plain_rows(store, width=30)
    wide = _plain_rows(store, width=120)

    assert max(len(row) for row in narrow) <= 30
    assert len(narrow) > 1
    assert wide == [_PARAGRAPH]


def test_captured_output_drops_old_width_padding_but_keeps_painted_cells() -> None:
    padded = Text("ab" + " " * 40)
    assert trim_row_padding(padded).plain == "ab"

    code_bar = Text("ab")
    code_bar.append("    ", style="on grey23")
    assert trim_row_padding(code_bar).plain == "ab    "


def test_padded_rows_do_not_split_into_blank_rows_when_narrowed() -> None:
    # A row padded to a 140-column launch must stay one row at 60 columns.
    store = TranscriptStore()
    store.append_text("Welcome to OpenSRE" + " " * 122 + "\n")

    assert _plain_rows(store, width=60) == ["Welcome to OpenSRE"]


def test_launch_banner_fits_every_width() -> None:
    store = TranscriptStore()
    store.append_renderable(ResponsiveLaunchBanner())

    for width in (40, 78, 140):
        rows = _plain_rows(store, width=width, count=200)
        assert any("Welcome to OpenSRE" in row for row in rows)
        assert max(len(row) for row in rows) <= width


def test_clear_screen_sequence_starts_the_transcript_over() -> None:
    store = TranscriptStore()
    store.append_text("old turn\n")
    store.append_text("\x1b[2J\x1b[Hnew start\n")

    assert _plain_rows(store, width=40) == ["new start"]


def test_carriage_return_overwrites_like_a_terminal_row() -> None:
    store = TranscriptStore()
    store.append_text("working 10%\rworking 90%\rdone\n")

    assert _plain_rows(store, width=40) == ["done"]


def test_scrollback_catch_up_skips_what_is_already_there() -> None:
    store = TranscriptStore()
    store.append_text("printed before the app\n", on_normal_screen=True)
    store.append_text("shown only full screen\n")

    first = store.take_unflushed()
    second = store.take_unflushed()

    assert [entry.renderable.plain for entry in first] == ["shown only full screen"]  # type: ignore[attr-defined]
    assert second == []


def test_entry_cap_never_drops_output_that_has_not_reached_scrollback() -> None:
    store = TranscriptStore(max_entries=2)
    for index in range(5):
        store.append_renderable(Text(f"turn {index}"))

    flushed = store.take_unflushed()
    store.append_renderable(Text("turn 5"))

    assert [entry.renderable.plain for entry in flushed] == [f"turn {i}" for i in range(5)]  # type: ignore[attr-defined]
    assert _plain_rows(store, width=40) == ["turn 4", "turn 5"]


def _visible(view: TranscriptControl, *, width: int = 20, height: int = 3) -> list[str]:
    content = view.create_content(width=width, height=height)
    return ["".join(text for _style, text in content.get_line(i)).rstrip() for i in range(height)]


def test_scrolled_back_view_holds_its_passage_while_output_streams() -> None:
    store = TranscriptStore()
    store.append_text("".join(f"line {index}\n" for index in range(10)))
    view = TranscriptControl(store)
    view.scroll(4)
    before = _visible(view)

    store.append_text("partial reply")
    assert _visible(view) == before
    store.append_text(" finished\nline 11\nline 12\n")
    assert _visible(view) == before

    store.append_text("an unfinished line that wraps once the window is narrow")
    held = _visible(view, width=60)
    _visible(view, width=20)
    assert _visible(view, width=60) == held

    store.clear()
    store.append_text("fresh\n")
    assert _visible(view)[-1] == "fresh"


def test_view_cannot_scroll_past_the_oldest_row() -> None:
    store = TranscriptStore()
    store.append_text("".join(f"line {index}\n" for index in range(5)))
    view = TranscriptControl(store)

    view.scroll(100)
    content = view.create_content(width=20, height=3)
    lines = ["".join(text for _style, text in content.get_line(i)).rstrip() for i in range(3)]

    assert lines == ["line 0", "line 1", "line 2"]
    view.scroll_to_bottom()
    content = view.create_content(width=20, height=3)
    assert "".join(text for _style, text in content.get_line(2)).rstrip() == "line 4"


class _FakeApp:
    def __init__(self, loop: asyncio.AbstractEventLoop | None) -> None:
        self.is_running = loop is not None
        self.loop = loop
        self.context = None


def _proxy(store: TranscriptStore, terminal: io.StringIO, app: _FakeApp) -> _AppBoundStdoutProxy:
    proxy = _AppBoundStdoutProxy(app, raw=True, transcript=store)  # type: ignore[arg-type]
    proxy._output = Vt100_Output(  # type: ignore[assignment]
        terminal, get_size=lambda: Size(rows=24, columns=80), term="xterm", enable_cpr=False
    )
    return proxy


def test_menu_paint_reaches_the_terminal_but_not_the_transcript() -> None:
    store = TranscriptStore()
    terminal = io.StringIO()
    proxy = _proxy(store, terminal, _FakeApp(None))

    proxy.write("reply line\n")
    proxy.begin_transient_output()
    proxy.write("\x1b[3A\x1b[J❯ (A) option\n")
    proxy.end_transient_output()
    proxy.write("after menu\n")
    proxy.close()

    assert "(A) option" in terminal.getvalue()
    assert _plain_rows(store, width=40) == ["reply line", "after menu"]


def test_output_cannot_forge_the_transient_marker() -> None:
    store = TranscriptStore()
    terminal = io.StringIO()
    proxy = _proxy(store, terminal, _FakeApp(None))

    proxy.write("log \x00opensre-transient-start\x00 line\n")
    proxy.write("next line\n")
    proxy.close()

    assert _plain_rows(store, width=60) == ["log opensre-transient-start line", "next line"]


def test_output_while_full_screen_is_recorded_without_touching_the_terminal() -> None:
    store = TranscriptStore()
    terminal = io.StringIO()
    loop = asyncio.new_event_loop()
    try:
        proxy = _proxy(store, terminal, _FakeApp(loop))
        proxy.write("streamed reply\n")
        proxy.close()
    finally:
        loop.close()

    assert terminal.getvalue() == ""
    assert _plain_rows(store, width=40) == ["streamed reply"]
    [entry] = store.take_unflushed()
    assert entry.on_normal_screen is True


def test_render_width_holds_on_a_dumb_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "dumb")

    rows = render_rows(Text(_PARAGRAPH), 30)

    assert max(len("".join(text for _style, text in row)) for row in rows) <= 30


def test_printed_tables_keep_their_layout_and_theme_at_every_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TranscriptStore()
    loop = asyncio.new_event_loop()
    try:
        proxy = _proxy(store, io.StringIO(), _FakeApp(loop))
        monkeypatch.setattr(sys, "stdout", proxy)
        console = Console(width=100)
        table = Table("service", "status")
        table.add_row("checkout-api", Text("degraded", style="status.bad"))
        with console.use_theme(Theme({"status.bad": "red"})):
            assert record_in_transcript(console, table)
        proxy.close()
    finally:
        loop.close()

    narrow, _older = store.tail_rows(24, 50)
    wide = _plain_rows(store, width=100)
    assert max(len("".join(text for _style, text in row).rstrip()) for row in narrow) <= 24
    assert any("checkout-api" in row and "degraded" in row for row in wide)
    red = ["".join(text for style, text in row if "red" in style) for row in narrow]
    assert "degraded" in red


class _Spinner:
    bytes_in = 0
    streaming = True

    def stop(self) -> None:
        """Nothing to stop."""


def test_replies_through_a_delegating_console_rewrap_on_grow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TranscriptStore()
    loop = asyncio.new_event_loop()
    try:
        proxy = _proxy(store, io.StringIO(), _FakeApp(loop))
        monkeypatch.setattr(sys, "stdout", proxy)
        console = StreamingConsole(
            _Spinner(), threading.Event(), output=Console(width=40), highlight=False
        )
        console.print(Markdown(_PARAGRAPH), width=38)
        proxy.close()
    finally:
        loop.close()

    assert _plain_rows(store, width=140) == [_PARAGRAPH]


def _race_the_end_marker(proxy: _AppBoundStdoutProxy) -> list[threading.Thread]:
    """Start a writer thread just as the menu's end marker is queued."""
    writers: list[threading.Thread] = []
    put = proxy._flush_queue.put

    def racing_put(item: str, block: bool = True, timeout: float | None = None) -> None:
        if item == _TRANSIENT_END and not writers:
            writer = threading.Thread(target=proxy.write, args=("after the menu\n",))
            writers.append(writer)
            writer.start()
            # Unless the proxy holds its write lock here, the line lands first.
            writer.join(timeout=0.2)
        put(item, block, timeout)

    proxy._flush_queue.put = racing_put  # type: ignore[method-assign]
    return writers


def test_output_racing_the_menu_end_is_still_recorded() -> None:
    store = TranscriptStore()
    proxy = _proxy(store, io.StringIO(), _FakeApp(None))
    writers = _race_the_end_marker(proxy)

    proxy.begin_transient_output()
    proxy.write("❯ (A) option\n")
    proxy.end_transient_output()
    [writer] = writers
    writer.join()
    proxy.close()

    assert _plain_rows(store, width=40) == ["after the menu"]

"""Live prompt region stays anchored and redraws cleanly across resize."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from prompt_toolkit.layout.containers import HSplit, VerticalAlign, Window
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.layout.screen import Char
from prompt_toolkit.output.base import Size
from prompt_toolkit.output.vt100 import Vt100_Output

from surfaces.interactive_shell.ui.input_prompt import build_prompt_session
from surfaces.interactive_shell.ui.input_prompt.resize import (
    _reflowed_rows_above_cursor,
    _tail_within_width,
    install_shrink_resize_guard,
    live_region_height_cap,
    prepare_live_region_height,
)
from surfaces.interactive_shell.ui.input_prompt.synchronized import synchronized_output


@dataclass
class _Cursor:
    x: int
    y: int


@dataclass
class _Screen:
    height: int


class _NativeOutput:
    """Minimal non-VT output matching native Win32 write behavior."""

    def __init__(self) -> None:
        self.writes: list[str] = []
        self.flush_count = 0

    def write_raw(self, text: str) -> None:
        self.writes.append(text)

    def flush(self) -> None:
        self.flush_count += 1


class _ScheduledResize:
    """Deterministic stand-in for an asyncio resize timer."""

    def __init__(self, callback: Any) -> None:
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _ResizeLoop:
    """Collect delayed callbacks without sleeping in resize tests."""

    def __init__(self) -> None:
        self.scheduled: list[_ScheduledResize] = []

    def call_later(self, _delay: float, callback: Any) -> _ScheduledResize:
        scheduled = _ScheduledResize(callback)
        self.scheduled.append(scheduled)
        return scheduled


def _row(text: str, *, width: int) -> dict[int, Any]:
    """A rendered frame row: *text*, padded out to *width* the way the UI pads."""
    return {column: SimpleNamespace(char=char) for column, char in enumerate(text.ljust(width))}


def _styled_row(text: str, *, width: int) -> dict[int, Any]:
    """A row whose padding carries a visible prompt-toolkit style."""
    return {
        column: SimpleNamespace(char=char, style="class:status")
        for column, char in enumerate(text.ljust(width))
    }


def test_prompt_root_hsplit_is_top_aligned_not_justify() -> None:
    async def _run() -> None:
        ps = build_prompt_session()
        root = ps.app.layout.container
        assert isinstance(root, HSplit)
        assert root.align is VerticalAlign.TOP

    asyncio.run(_run())


def test_live_region_height_cap_is_tight() -> None:
    assert live_region_height_cap(5) == 6
    assert live_region_height_cap(20) == 12


def test_deferred_input_tail_uses_terminal_cell_width() -> None:
    assert _tail_within_width("ab界", 3) == "b界"
    assert _tail_within_width("ab界", 2) == "界"
    assert _tail_within_width("x界́", 2) == "界́"
    assert _tail_within_width("x界́", 1) == ""


def test_synchronized_output_skips_private_mode_bytes_for_non_vt_output() -> None:
    output = _NativeOutput()

    with synchronized_output(output):
        output.write_raw("frame")
        output.flush()

    assert output.writes == ["frame"]
    assert output.flush_count == 1


def test_synchronized_output_coalesces_nested_frames() -> None:
    terminal = io.StringIO()
    output = Vt100_Output(
        terminal,
        get_size=lambda: Size(rows=30, columns=80),
        term="xterm-256color",
        enable_cpr=False,
    )

    with synchronized_output(output):
        output.write_raw("outer")
        with synchronized_output(output):
            output.write_raw("inner")

    emitted = terminal.getvalue()
    assert emitted.count("\x1b[?2026h") == 1
    assert emitted.count("\x1b[?2026l") == 1
    assert emitted.index("\x1b[?2026h") < emitted.index("outer")
    assert emitted.index("inner") < emitted.index("\x1b[?2026l")


def test_prepare_live_region_height_zeros_cpr_and_drops_tall_last_screen() -> None:
    layout = Layout(Window(height=5))
    renderer = MagicMock()
    renderer._min_available_height = 40
    renderer._last_screen = _Screen(height=28)

    cap = prepare_live_region_height(renderer, layout, columns=80, rows=50)

    assert cap == live_region_height_cap(5)
    assert renderer._min_available_height == 0
    assert renderer._last_screen is None


def test_cpr_report_discards_fill_to_floor() -> None:
    terminal = io.StringIO()
    output = Vt100_Output(
        terminal,
        get_size=lambda: Size(rows=40, columns=80),
        term="xterm-256color",
        enable_cpr=False,
    )
    app: Any = MagicMock()
    app.output = output
    renderer = MagicMock()
    renderer._cursor_pos = _Cursor(x=0, y=1)
    renderer._min_available_height = 0
    renderer._last_screen = None
    renderer._last_size = Size(rows=40, columns=80)

    def _original_report(row: int) -> None:
        del row
        renderer._min_available_height = 35

    renderer.report_absolute_cursor_row = _original_report
    renderer.render = MagicMock()
    app.renderer = renderer
    app._on_resize = MagicMock()
    app._request_absolute_cursor_position = MagicMock()
    app._redraw = MagicMock()
    # A MagicMock would hand back a truthy stand-in for this flag.
    app._running_in_terminal = False

    install_shrink_resize_guard(app)
    renderer.report_absolute_cursor_row(5)

    assert renderer._min_available_height == 0


def test_empty_shell_resize_uses_existing_banner_repaint_hook() -> None:
    output = Vt100_Output(
        io.StringIO(),
        get_size=lambda: Size(rows=30, columns=80),
        term="xterm-256color",
        enable_cpr=False,
    )
    app: Any = MagicMock()
    app.output = output
    renderer = MagicMock()
    renderer._min_available_height = 0
    renderer._last_screen = None
    renderer.render = MagicMock()
    renderer.reset = MagicMock()
    app.renderer = renderer
    original_on_resize = MagicMock()
    app._on_resize = original_on_resize
    app._request_absolute_cursor_position = MagicMock()
    app._redraw = MagicMock(side_effect=lambda: output.write_raw("prompt"))
    app._running_in_terminal = False
    repaint_calls: list[bool] = []

    install_shrink_resize_guard(
        app,
        rerender_banner=lambda: repaint_calls.append(True) or "banner row one\nbanner row two\n",
    )
    output.stdout.seek(0)
    output.stdout.truncate(0)
    app._on_resize()

    assert repaint_calls == [True]
    emitted = output.stdout.getvalue()
    frame_start = emitted.find("\x1b[?2026h")
    clear = emitted.find("\x1b[2J")
    banner = emitted.find("banner row one\r\nbanner row two\r\n")
    prompt = emitted.find("prompt")
    frame_end = emitted.find("\x1b[?2026l")
    assert -1 < frame_start < clear < banner < prompt < frame_end
    original_on_resize.assert_not_called()
    renderer.reset.assert_called_once_with(leave_alternate_screen=False)
    app._request_absolute_cursor_position.assert_called_once_with()
    app._redraw.assert_called_once_with()


def _painted_resize_app(
    size_state: list[Size] | None = None,
) -> tuple[Any, Any, io.StringIO]:
    """Return an app with one painted live frame and mutable terminal size."""
    if size_state is None:
        size_state = [Size(rows=30, columns=90)]
    terminal = io.StringIO()
    output = Vt100_Output(
        terminal,
        get_size=lambda: size_state[0],
        term="xterm-256color",
        enable_cpr=False,
    )
    app: Any = MagicMock()
    app.output = output
    renderer = MagicMock()
    renderer._min_available_height = 0
    renderer._last_size = Size(rows=30, columns=110)
    renderer._last_screen = None
    renderer.reset = MagicMock()
    renderer._original_render_count = 0

    def _render(*_args: object, **_kwargs: object) -> None:
        renderer._original_render_count += 1
        renderer._cursor_pos = _Cursor(x=4, y=2)
        renderer._last_size = output.get_size()
        renderer._last_screen = SimpleNamespace(
            height=4,
            show_cursor=False,
            data_buffer={row: _row("x" * 109, width=109) for row in range(4)},
        )

    renderer.render = _render
    app.renderer = renderer
    app._on_resize = MagicMock()
    app._request_absolute_cursor_position = MagicMock()
    app._redraw = MagicMock()
    # A MagicMock would hand back a truthy stand-in for this flag.
    app._running_in_terminal = False

    install_shrink_resize_guard(app)
    # Paint a frame — only then is there a live region to erase.
    renderer.render(app, Layout(Window(height=3)))
    terminal.seek(0)
    terminal.truncate(0)
    return app, renderer, terminal


def test_resize_erases_from_reflowed_live_region_top() -> None:
    """The settled erase moves only through the reflowed live frame."""
    app, renderer, terminal = _painted_resize_app()

    app._on_resize()

    emitted = terminal.getvalue()
    carriage_return = emitted.find("\r")
    upward = emitted.find("\x1b[4A")
    erase = emitted.find("\x1b[J")
    assert -1 < carriage_return < upward < erase
    assert "\x1b[4D" not in emitted
    assert "\x1b[5A" not in emitted
    renderer.reset.assert_called_once_with(leave_alternate_screen=False)
    app._request_absolute_cursor_position.assert_not_called()
    app._redraw.assert_called_once()


def test_resize_burst_waits_for_dimensions_to_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    """A window drag must repaint once, after its final resize signal."""
    app, _renderer, terminal = _painted_resize_app()
    loop = _ResizeLoop()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: loop)

    app._on_resize()
    app._on_resize()
    app._on_resize()

    assert [scheduled.cancelled for scheduled in loop.scheduled] == [True, True, False]
    assert "\x1b[J" not in terminal.getvalue()
    app._redraw.assert_not_called()

    loop.scheduled[-1].callback()

    assert terminal.getvalue().count("\x1b[J") == 1
    app._redraw.assert_called_once_with()


def test_render_waits_while_resize_dimensions_are_unstable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Typing during a drag must not repaint against an intermediate width."""
    size_state = [Size(rows=30, columns=90)]
    app, renderer, terminal = _painted_resize_app(size_state)
    loop = _ResizeLoop()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: loop)
    app._redraw.side_effect = lambda: renderer.render(app, Layout(Window(height=3)))
    initial_render_count = renderer._original_render_count

    size_state[0] = Size(rows=20, columns=70)
    app._on_resize()
    renderer.render(app, Layout(Window(height=3)))

    assert renderer._original_render_count == initial_render_count
    assert "\x1b[J" not in terminal.getvalue()

    loop.scheduled[-1].callback()

    assert renderer._original_render_count == initial_render_count + 1
    assert terminal.getvalue().count("\x1b[J") == 1


def test_final_render_restores_terminal_autowrap() -> None:
    app, renderer, terminal = _painted_resize_app()

    renderer.render(app, Layout(Window(height=3)), is_done=True)
    app.output.flush()

    emitted = terminal.getvalue()
    assert emitted.rfind("\x1b[?7h") > emitted.rfind("\x1b[?7l")


def test_resize_defers_repaint_until_live_region_is_cursor_addressable() -> None:
    """A live region taller than the viewport cannot be erased from its top."""
    size_state = [Size(rows=30, columns=90)]
    app, renderer, terminal = _painted_resize_app(size_state)
    initial_render_count = renderer._original_render_count

    size_state[0] = Size(rows=5, columns=20)
    app._on_resize()
    renderer.render(app, Layout(Window(height=3)))

    assert "\x1b[J" not in terminal.getvalue()
    assert renderer._original_render_count == initial_render_count
    app._redraw.assert_not_called()

    size_state[0] = Size(rows=30, columns=90)
    app._redraw.side_effect = lambda: renderer.render(app, Layout(Window(height=3)))
    app._on_resize()

    assert terminal.getvalue().count("\x1b[J") == 1
    assert renderer._original_render_count == initial_render_count + 1


def test_deferred_resize_keeps_the_editable_line_responsive() -> None:
    """A too-short viewport still shows buffer edits instead of looking hung."""
    size_state = [Size(rows=30, columns=90)]
    app, renderer, terminal = _painted_resize_app(size_state)
    app.current_buffer.document.current_line_before_cursor = "typed while tiny"
    app.current_buffer.document.current_line_after_cursor = ""
    initial_render_count = renderer._original_render_count

    size_state[0] = Size(rows=3, columns=20)
    app._on_resize()
    renderer.render(app, Layout(Window(height=3)))

    assert "> typed while tiny▌" in terminal.getvalue()
    assert renderer._original_render_count == initial_render_count


def test_deferred_compact_row_keeps_wrapped_cursor_offset_on_regrow() -> None:
    """Recovery erases chrome above a compact row born on a soft wrap."""
    size_state = [Size(rows=30, columns=95)]
    app, renderer, terminal = _painted_resize_app(size_state)
    renderer._cursor_pos = _Cursor(x=34, y=2)
    renderer._last_size = Size(rows=30, columns=95)
    renderer._style_string_has_style = {"": False}
    renderer._last_screen = SimpleNamespace(
        height=4,
        show_cursor=False,
        data_buffer={
            0: _row("Auto (High) · Allow all · CI/CD fixes (0)", width=94),
            1: _row("╭" + "─" * 92 + "╮", width=94),
            2: _row("│ > long draft with a wrapped cursor".ljust(93) + "│", width=94),
        },
    )
    app.current_buffer.document.current_line_before_cursor = "long draft with a wrapped cursor"
    app.current_buffer.document.current_line_after_cursor = ""

    size_state[0] = Size(rows=3, columns=33)
    app._on_resize()
    renderer.render(app, Layout(Window(height=3)))

    size_state[0] = Size(rows=9, columns=80)
    app._redraw.side_effect = lambda: renderer.render(app, Layout(Window(height=3)))
    app._on_resize()

    emitted = terminal.getvalue()
    assert "\x1b[4A" in emitted
    assert "\x1b[3A" not in emitted


def test_deferred_resize_does_not_count_an_unpainted_compact_row() -> None:
    """Recovery does not erase above the live region before compact paint."""
    size_state = [Size(rows=30, columns=95)]
    app, renderer, terminal = _painted_resize_app(size_state)
    renderer._cursor_pos = _Cursor(x=34, y=2)
    renderer._last_size = Size(rows=30, columns=95)
    renderer._style_string_has_style = {"": False}
    renderer._last_screen = SimpleNamespace(
        height=4,
        show_cursor=False,
        data_buffer={
            0: _row("Auto (High) · Allow all · CI/CD fixes (0)", width=94),
            1: _row("╭" + "─" * 92 + "╮", width=94),
            2: _row("│ > long draft with a wrapped cursor".ljust(93) + "│", width=94),
        },
    )

    size_state[0] = Size(rows=3, columns=33)
    app._on_resize()

    size_state[0] = Size(rows=9, columns=80)
    app._redraw.side_effect = lambda: renderer.render(app, Layout(Window(height=3)))
    app._on_resize()

    emitted = terminal.getvalue()
    assert "\x1b[3A" in emitted
    assert "\x1b[4A" not in emitted


def test_deferred_resize_shows_text_on_both_sides_of_the_caret() -> None:
    size_state = [Size(rows=30, columns=90)]
    app, renderer, terminal = _painted_resize_app(size_state)
    app.current_buffer.document.current_line_before_cursor = "ab"
    app.current_buffer.document.current_line_after_cursor = "c"

    size_state[0] = Size(rows=3, columns=20)
    app._on_resize()
    renderer.render(app, Layout(Window(height=3)))

    assert "> ab▌c" in terminal.getvalue()


def test_deferred_resize_renders_control_characters_as_text() -> None:
    size_state = [Size(rows=30, columns=90)]
    app, renderer, terminal = _painted_resize_app(size_state)
    app.current_buffer.document.current_line_before_cursor = "a\tb\x07"
    app.current_buffer.document.current_line_after_cursor = ""

    size_state[0] = Size(rows=3, columns=20)
    app._on_resize()
    renderer.render(app, Layout(Window(height=3)))

    emitted = terminal.getvalue()
    assert "a␉b␇▌" in emitted
    assert "\t" not in emitted
    assert "\x07" not in emitted


def test_deferred_final_render_restores_modes_without_full_repaint() -> None:
    size_state = [Size(rows=30, columns=90)]
    app, renderer, terminal = _painted_resize_app(size_state)
    app.current_buffer.document.current_line_before_cursor = "draft"
    app.current_buffer.document.current_line_after_cursor = ""
    initial_render_count = renderer._original_render_count

    size_state[0] = Size(rows=3, columns=20)
    app._on_resize()
    renderer.render(app, Layout(Window(height=3)), is_done=True)

    emitted = terminal.getvalue()
    assert renderer._original_render_count == initial_render_count
    compact_line = emitted.find("> draft▌")
    assert compact_line >= 0
    assert emitted.find("\r\n", compact_line) > compact_line
    assert emitted.rfind("\x1b[?7h") > emitted.rfind("\x1b[?7l")


def test_resize_erase_and_repaint_are_one_synchronized_frame() -> None:
    """Erase-then-draw is two visible states unless the terminal holds them.

    ``_redraw`` paints inline, so the replacement prompt exists before the
    frame closes and the gap never reaches the screen.
    """
    app, _renderer, terminal = _painted_resize_app()

    app._on_resize()

    emitted = terminal.getvalue()
    start, erase, end = (
        emitted.find("\x1b[?2026h"),
        emitted.find("\x1b[J"),
        emitted.find("\x1b[?2026l"),
    )
    assert -1 < start < erase < end


def test_resize_during_background_output_opens_no_frame() -> None:
    """The stdout proxy owns the same private mode, and it does not nest.

    While the app runs something in the terminal, ``_redraw`` paints nothing
    and the proxy drives the very same toggles — a frame opened here would be
    closed by the proxy's end marker, presenting the bare erase.
    """
    app, _renderer, terminal = _painted_resize_app()
    app._running_in_terminal = True

    app._on_resize()

    emitted = terminal.getvalue()
    assert "\x1b[?2026h" not in emitted
    assert "\x1b[?2026l" not in emitted
    assert "\x1b[?7l" not in emitted
    assert "\x1b[J" not in emitted


def test_native_output_uses_prompt_toolkit_resize_instead_of_vt_reflow_math() -> None:
    """Native consoles use screen-buffer coordinates, not VT row reflow."""
    output = MagicMock()
    output.get_size.return_value = Size(rows=30, columns=90)
    app: Any = MagicMock()
    app.output = output
    renderer = MagicMock()
    renderer._min_available_height = 0
    renderer._last_screen = SimpleNamespace(
        height=2,
        data_buffer={0: _row("status", width=109)},
    )
    renderer._cursor_pos = _Cursor(x=4, y=1)
    renderer.report_absolute_cursor_row = MagicMock()
    renderer.render = MagicMock()
    app.renderer = renderer
    original_on_resize = MagicMock()
    app._on_resize = original_on_resize
    app._running_in_terminal = False

    install_shrink_resize_guard(app)
    output.reset_mock()
    app._on_resize()

    original_on_resize.assert_called_once_with()
    output.cursor_backward.assert_not_called()
    output.cursor_up.assert_not_called()
    output.erase_down.assert_not_called()


def test_resize_restores_the_terminal_when_the_repaint_raises() -> None:
    """A frame left open would hold the display until the terminal times out."""
    app, _renderer, terminal = _painted_resize_app()
    app._redraw.side_effect = RuntimeError("repaint failed")

    with pytest.raises(RuntimeError):
        app._on_resize()

    # The frame is closed on the way out, so the display is never left held.
    assert terminal.getvalue().endswith("\x1b[?2026l")


def test_shrink_resize_guard_leaves_hardware_cursor_at_logical_input() -> None:
    terminal = io.StringIO()
    output = Vt100_Output(
        terminal,
        get_size=lambda: Size(rows=24, columns=80),
        term="xterm-256color",
        enable_cpr=False,
    )
    hidden: list[bool] = []
    real_hide = output.hide_cursor

    def _spy_hide() -> None:
        hidden.append(True)
        real_hide()

    output.hide_cursor = _spy_hide  # type: ignore[method-assign]

    app: Any = MagicMock()
    app.output = output
    renderer = MagicMock()
    renderer._cursor_pos = _Cursor(x=0, y=1)
    renderer._min_available_height = 0
    renderer._last_screen = SimpleNamespace(
        height=2,
        show_cursor=False,
        data_buffer={0: _row("status", width=79), 1: _row("input", width=79)},
    )
    renderer._last_size = Size(rows=24, columns=80)
    renderer.report_absolute_cursor_row = MagicMock()
    calls: list[str] = []

    def _original_render(*_a: object, **_k: object) -> None:
        calls.append("render")

    renderer.render = _original_render
    app.renderer = renderer
    app._on_resize = MagicMock()
    app._request_absolute_cursor_position = MagicMock()
    app._redraw = MagicMock()
    # A MagicMock would hand back a truthy stand-in for this flag.
    app._running_in_terminal = False

    install_shrink_resize_guard(app)
    hidden.clear()
    app.renderer.render(app, Layout(Window()))
    assert calls == ["render"]
    assert hidden == []
    assert "\x1b[A" not in terminal.getvalue()


def test_reflow_count_ignores_the_padding_prompt_toolkit_writes() -> None:
    """A terminal reflows a row by its content, not by its trailing blanks.

    Restoring the hardware cursor from its parked anchor must move through the
    physical rows the terminal actually created, not prompt-toolkit padding.
    """
    # Arrange: a 189-wide frame — a full status row, then a blank row that is
    # padded to the same width — with the cursor on the third row.
    renderer = SimpleNamespace(
        _last_screen=SimpleNamespace(
            data_buffer={
                0: _row(
                    "Auto (High) \u00b7 Allow all".ljust(172) + "\u00b7 CI/CD fixes (0)", width=189
                ),
                1: _row("", width=189),
            }
        ),
        _cursor_pos=_Cursor(x=0, y=2),
    )

    rows = _reflowed_rows_above_cursor(renderer, columns=100)

    # The status row really does wrap onto two rows at 100 columns; the blank
    # one does not wrap at all. Counting its padding would say four.
    assert rows == 3


def test_reflow_count_includes_styled_trailing_blanks() -> None:
    """Painted padding is terminal content and gains physical rows on shrink."""
    renderer = SimpleNamespace(
        _last_screen=SimpleNamespace(
            data_buffer={
                0: _styled_row("Auto (High) · Allow all", width=119),
                1: _row("╭" + "─" * 117 + "╮", width=119),
            }
        ),
        _cursor_pos=_Cursor(x=1, y=2),
        _style_string_has_style={"class:status": True},
    )

    rows = _reflowed_rows_above_cursor(renderer, columns=80)

    assert rows == 4


def test_reflow_count_includes_wide_glyph_continuation_cell() -> None:
    renderer = SimpleNamespace(
        _last_screen=SimpleNamespace(
            data_buffer={
                0: {
                    79: Char("界"),
                    80: Char(""),
                }
            }
        ),
        _cursor_pos=_Cursor(x=0, y=1),
    )

    rows = _reflowed_rows_above_cursor(renderer, columns=80)

    assert rows == 2

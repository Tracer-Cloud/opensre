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
from prompt_toolkit.output.base import Size
from prompt_toolkit.output.vt100 import Vt100_Output

from surfaces.interactive_shell.ui.input_prompt import build_prompt_session
from surfaces.interactive_shell.ui.input_prompt.resize import (
    _reflowed_rows_above_cursor,
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


def test_synchronized_output_skips_private_mode_bytes_for_non_vt_output() -> None:
    output = _NativeOutput()

    with synchronized_output(output):
        output.write_raw("frame")
        output.flush()

    assert output.writes == ["frame"]
    assert output.flush_count == 1


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


def test_resize_with_banner_hook_skips_partial_erase_and_redraws() -> None:
    """Partial erase stacks Auto/composer ghosts; chrome reset must replace it."""
    terminal = io.StringIO()
    output = Vt100_Output(
        terminal,
        get_size=lambda: Size(rows=30, columns=80),
        term="xterm-256color",
        enable_cpr=False,
    )
    app: Any = MagicMock()
    app.output = output
    renderer = MagicMock()
    renderer._cursor_pos = _Cursor(x=2, y=4)
    renderer._last_screen = _Screen(height=10)
    renderer._min_available_height = 10
    renderer.render = MagicMock()
    renderer.reset = MagicMock()
    app.renderer = renderer
    original_on_resize = MagicMock()
    app._on_resize = original_on_resize
    app._request_absolute_cursor_position = MagicMock()
    app._redraw = MagicMock()
    app._running_in_terminal = False

    banner_calls: list[int] = []

    def _rerender() -> bool:
        banner_calls.append(1)
        return True

    install_shrink_resize_guard(app, rerender_banner=_rerender)
    app._on_resize()

    assert banner_calls == [1]
    original_on_resize.assert_not_called()
    renderer.reset.assert_called_once_with(leave_alternate_screen=False)
    app._request_absolute_cursor_position.assert_not_called()
    app._redraw.assert_called_once()
    assert renderer._min_available_height == 0
    assert renderer._last_screen is None


def test_resize_uses_prompt_toolkit_path_for_a_visible_hardware_cursor() -> None:
    """Search and system controls keep their native cursor and resize handling."""
    output = Vt100_Output(
        io.StringIO(),
        get_size=lambda: Size(rows=30, columns=80),
        term="xterm-256color",
        enable_cpr=False,
    )
    app: Any = MagicMock()
    app.output = output
    renderer = MagicMock()
    renderer._cursor_pos = _Cursor(x=1, y=1)
    renderer._last_size = Size(rows=30, columns=80)
    renderer._last_screen = SimpleNamespace(
        height=2,
        show_cursor=True,
        data_buffer={0: _row("search", width=79), 1: _row("input", width=79)},
    )
    renderer._min_available_height = 0
    renderer.report_absolute_cursor_row = MagicMock()
    renderer.render = MagicMock()
    renderer.erase = MagicMock()
    app.renderer = renderer
    original_on_resize = MagicMock()
    app._on_resize = original_on_resize
    app._running_in_terminal = False

    install_shrink_resize_guard(app)
    renderer.render(app, Layout(Window(height=2)))
    app._on_resize()

    original_on_resize.assert_called_once_with()
    app._redraw.assert_not_called()


def _painted_resize_app() -> tuple[Any, Any, io.StringIO]:
    """An app whose live region is painted with its terminal cursor anchored."""
    terminal = io.StringIO()
    output = Vt100_Output(
        terminal,
        get_size=lambda: Size(rows=30, columns=90),
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

    def _render(*_args: object, **_kwargs: object) -> None:
        renderer._cursor_pos = _Cursor(x=4, y=2)
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

    install_shrink_resize_guard(app, rerender_banner=lambda: False)
    # Paint a frame — only then is there a live region to erase.
    renderer.render(app, Layout(Window(height=3)))
    terminal.seek(0)
    terminal.truncate(0)
    return app, renderer, terminal


def test_resize_erases_from_parked_live_region_anchor() -> None:
    """The resize erase starts at prompt top without moving into transcript."""
    app, renderer, terminal = _painted_resize_app()

    app._on_resize()

    emitted = terminal.getvalue()
    assert "\x1b[J" in emitted
    assert "\x1b[A" not in emitted
    assert "\x1b[2A" not in emitted
    renderer.reset.assert_called_once_with(leave_alternate_screen=False)
    app._request_absolute_cursor_position.assert_not_called()
    app._redraw.assert_called_once()


def test_prompt_toolkit_erase_uses_parked_live_region_anchor() -> None:
    """Background output must not restore then erase through transcript rows."""
    _app, renderer, terminal = _painted_resize_app()

    renderer.erase(leave_alternate_screen=False)

    emitted = terminal.getvalue()
    assert "\x1b[J" in emitted
    assert "\x1b[A" not in emitted
    assert "\x1b[B" not in emitted
    renderer.reset.assert_called_once_with(leave_alternate_screen=False)


def test_resize_burst_erases_once_per_painted_frame() -> None:
    """Dragging an edge can send several signals before a repaint runs.

    Only the first has a parked frame to erase. Erasing again would remove
    whatever output now follows the cleared live-region anchor.
    """
    app, _renderer, terminal = _painted_resize_app()

    app._on_resize()
    terminal.seek(0)
    terminal.truncate(0)
    app._on_resize()

    # Second signal: no repaint happened in between, so nothing to erase.
    assert "\x1b[J" not in terminal.getvalue()
    assert app._redraw.call_count == 2


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


def test_resize_restores_the_terminal_when_the_repaint_raises() -> None:
    """A frame left open would hold the display until the terminal times out."""
    app, _renderer, terminal = _painted_resize_app()
    app._redraw.side_effect = RuntimeError("repaint failed")

    with pytest.raises(RuntimeError):
        app._on_resize()

    # The frame is closed on the way out, so the display is never left held.
    assert terminal.getvalue().endswith("\x1b[?2026l")


def test_shrink_resize_guard_parks_and_hides_hardware_cursor_after_render() -> None:
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
    assert hidden == [True]
    assert "\x1b[?25l\x1b[?2026l" in terminal.getvalue()


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

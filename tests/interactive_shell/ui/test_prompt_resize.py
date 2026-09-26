"""Live prompt region stays compact; resize resets chrome instead of partial erase."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from prompt_toolkit.layout.containers import HSplit, VerticalAlign, Window
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.output.base import Size
from prompt_toolkit.output.vt100 import Vt100_Output

from surfaces.interactive_shell.ui.input_prompt import build_prompt_session
from surfaces.interactive_shell.ui.input_prompt.resize import (
    install_shrink_resize_guard,
    live_region_height_cap,
    prepare_live_region_height,
)


@dataclass
class _Cursor:
    x: int
    y: int


@dataclass
class _Screen:
    height: int


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

    banner_calls: list[int] = []

    def _rerender() -> bool:
        banner_calls.append(1)
        return True

    install_shrink_resize_guard(app, rerender_banner=_rerender)
    app._on_resize()

    assert banner_calls == [1]
    original_on_resize.assert_not_called()
    renderer.reset.assert_called_once_with(leave_alternate_screen=False)
    app._request_absolute_cursor_position.assert_called_once()
    app._redraw.assert_called_once()
    assert renderer._min_available_height == 0
    assert renderer._last_screen is None


def _painted_resize_app() -> tuple[Any, Any, io.StringIO]:
    """An app whose live region was painted at 110 columns, now shown at 90."""
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
    renderer._last_size = Size(rows=30, columns=90)
    renderer._last_screen = None
    renderer.render = MagicMock()
    renderer.reset = MagicMock()
    app.renderer = renderer
    app._on_resize = MagicMock()
    app._request_absolute_cursor_position = MagicMock()
    app._redraw = MagicMock()

    install_shrink_resize_guard(app, rerender_banner=lambda: False)
    # Paint a frame — only then is there a live region to erase.
    renderer.render(app, Layout(Window(height=3)))
    # That frame went out at 110 columns; the window has since shrunk to 90.
    renderer._cursor_pos = _Cursor(x=4, y=2)
    renderer._last_screen = SimpleNamespace(
        height=4,
        data_buffer={row: dict.fromkeys(range(109)) for row in range(4)},
    )
    renderer._last_size = Size(rows=30, columns=110)
    terminal.seek(0)
    terminal.truncate(0)
    return app, renderer, terminal


def test_resize_after_width_shrink_erases_reflowed_live_region() -> None:
    """Erase from the reflowed top row instead of leaving stale prompt chrome."""
    app, renderer, terminal = _painted_resize_app()

    app._on_resize()

    # Each 109-cell row reflows onto two rows at 90 columns, so the frame top
    # is four rows above the cursor, not the two prompt-toolkit recorded.
    assert "\x1b[4D\x1b[4A\x1b[J" in terminal.getvalue()
    renderer.reset.assert_called_once_with(leave_alternate_screen=False)
    app._request_absolute_cursor_position.assert_called_once()
    app._redraw.assert_called_once()


def test_resize_burst_erases_once_per_painted_frame() -> None:
    """Dragging an edge sends several signals before the CPR-gated repaint runs.

    Only the first has a frame to erase. Erasing again would start from the
    cursor that first erase reset — below the frame still on screen, which it
    would strand as one Auto/composer ghost per signal.
    """
    app, _renderer, terminal = _painted_resize_app()

    app._on_resize()
    terminal.seek(0)
    terminal.truncate(0)
    app._on_resize()

    # Second signal: no repaint happened in between, so nothing to erase.
    assert "\x1b[J" not in terminal.getvalue()
    assert app._redraw.call_count == 2


def test_resize_repaint_is_presented_as_one_synchronized_frame() -> None:
    """Erase-then-draw is two visible states unless the terminal holds them."""
    app, _renderer, terminal = _painted_resize_app()

    app._on_resize()

    emitted = terminal.getvalue()
    start, erase, end = (
        emitted.find("\x1b[?2026h"),
        emitted.find("\x1b[J"),
        emitted.find("\x1b[?2026l"),
    )
    assert -1 < start < erase < end


def test_shrink_resize_guard_disables_autowrap_after_render() -> None:
    terminal = io.StringIO()
    output = Vt100_Output(
        terminal,
        get_size=lambda: Size(rows=24, columns=80),
        term="xterm-256color",
        enable_cpr=False,
    )
    disabled: list[bool] = []
    real_disable = output.disable_autowrap

    def _spy_disable() -> None:
        disabled.append(True)
        real_disable()

    output.disable_autowrap = _spy_disable  # type: ignore[method-assign]

    app: Any = MagicMock()
    app.output = output
    renderer = MagicMock()
    renderer._cursor_pos = _Cursor(x=0, y=1)
    renderer._min_available_height = 0
    renderer._last_screen = None
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

    install_shrink_resize_guard(app)
    disabled.clear()
    app.renderer.render(app, Layout(Window()))
    assert calls == ["render"]
    assert disabled

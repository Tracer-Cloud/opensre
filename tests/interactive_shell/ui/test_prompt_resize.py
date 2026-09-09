"""Live prompt region stays compact; resize resets chrome instead of partial erase."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
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


def test_resize_during_a_turn_erases_the_live_region_instead_of_forgetting_it() -> None:
    """Reset without erase left one stale frame per resize signal, stacking down the screen."""
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
    renderer._last_screen = _Screen(height=6)
    renderer.reset = MagicMock()
    app.renderer = renderer
    original_on_resize = MagicMock()
    app._on_resize = original_on_resize
    app._redraw = MagicMock()

    install_shrink_resize_guard(app, rerender_banner=lambda: False)
    app._on_resize()

    # prompt-toolkit's own resize path erases from the top row and redraws.
    original_on_resize.assert_called_once()
    renderer.reset.assert_not_called()
    app._redraw.assert_not_called()


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

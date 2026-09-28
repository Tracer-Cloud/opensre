"""Keep the live prompt compact and preserve scrollback across resize.

Root cause
----------
prompt-toolkit sizes a non-fullscreen Screen as::

    height = max(_min_available_height, last_height, preferred_height)

After CPR, ``_min_available_height`` is "rows below the cursor" (the rest of the
terminal under the launch banner). That tall Screen scrolls the banner away,
and ``last_height`` sticks so later paints stay hollow.

Window drags emit bursts of SIGWINCH events while VTE is still reflowing. The
hardware cursor must remain at prompt-toolkit's logical input cursor during
that reflow; parking it at the live-region top lets VTE move that row into the
transcript. Resize paints are therefore coalesced after dimensions settle. The
old frame's measured physical rows then locate the live-region top for one
bounded erase and repaint, leaving transcript rows above it untouched.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.output.base import Size

from surfaces.interactive_shell.ui.input_prompt.synchronized import (
    supports_synchronized_output,
    synchronized_output,
)

# Soft-wrap headroom above preferred Auto + composer. Keep tiny — blank Screen
# rows below the composer become a hollow band and invite ghost stacking.
_LIVE_REGION_HEIGHT_PAD = 1
# Absolute ceiling; never paint a live Screen taller than this.
_LIVE_REGION_HARD_MAX = 12
# Long enough to coalesce the allocation/SIGWINCH bursts emitted by VTE and
# common window managers during an interactive drag.
_RESIZE_SETTLE_SECONDS = 0.2


def live_region_height_cap(preferred: int) -> int:
    """Return the max Screen height allowed for the live prompt region."""
    return min(max(preferred, 1) + _LIVE_REGION_HEIGHT_PAD, _LIVE_REGION_HARD_MAX)


def prepare_live_region_height(renderer: Any, layout: Layout, *, columns: int, rows: int) -> int:
    """Force CPR / last-screen budgets down so height tracks preferred chrome.

    Returns the live-region cap applied (for tests).
    """
    preferred = layout.container.preferred_height(columns, rows).preferred
    cap = live_region_height_cap(preferred)
    renderer._min_available_height = 0
    last = getattr(renderer, "_last_screen", None)
    if last is not None and int(getattr(last, "height", 0) or 0) > cap:
        renderer._last_screen = None
    return cap


# Back-compat name used by older tests / imports.
clamp_live_region_min_height = prepare_live_region_height


def _size_changed(previous: Size | None, current: Size) -> bool:
    if previous is None:
        return False
    return previous.rows != current.rows or previous.columns != current.columns


def _screen_row_width(
    screen: Any,
    row: int,
    style_string_has_style: Any | None = None,
) -> int:
    """Cells of *row* a terminal will reflow, including painted blanks."""
    data = getattr(screen, "data_buffer", {}).get(row, {})
    last = -1
    for column, char in data.items():
        text = getattr(char, "char", char)
        style = getattr(char, "style", "")
        painted_blank = bool(
            isinstance(text, str)
            and text
            and not text.strip()
            and style_string_has_style is not None
            and style_string_has_style[style]
        )
        if isinstance(text, str) and (text.strip() or painted_blank):
            last = max(last, column)
    return last + 1


def _reflowed_rows_above_cursor(renderer: Any, *, columns: int) -> int | None:
    """Return the physical row offset after the previous frame reflows."""
    screen = getattr(renderer, "_last_screen", None)
    cursor = getattr(renderer, "_cursor_pos", None)
    if screen is None or cursor is None:
        return None
    style_string_has_style = getattr(renderer, "_style_string_has_style", None)
    rows = int(cursor.x) // columns
    for row in range(cursor.y):
        width = _screen_row_width(screen, row, style_string_has_style)
        rows += max(1, (width + columns - 1) // columns)
    return rows


def install_shrink_resize_guard(
    app: Application[Any],
    *,
    rerender_banner: Callable[[], bool] | None = None,
) -> None:
    """Install compact-height and settled-resize guards for the live prompt.

    ``rerender_banner`` clears the viewport and reprints the static launch
    banner at the new size, returning True when it did so. Once transcript is
    present, only the measured live region is erased and repainted.
    """
    output = app.output
    renderer = app.renderer
    original_on_resize = app._on_resize
    original_render = renderer.render
    original_report = renderer.report_absolute_cursor_row
    resize_handle: asyncio.TimerHandle | None = None

    def report_absolute_cursor_row(row: int) -> None:
        original_report(row)
        renderer._min_available_height = 0

    def _render(pt_app: Any, layout: Layout, is_done: bool = False) -> None:
        nonlocal resize_handle
        if is_done and resize_handle is not None:
            resize_handle.cancel()
            resize_handle = None
        size = output.get_size()
        size_changed = _size_changed(getattr(renderer, "_last_size", None), size)
        if resize_handle is not None and size_changed and not is_done:
            return
        if size_changed:
            renderer._min_available_height = 0
        prepare_live_region_height(
            renderer,
            layout,
            columns=size.columns,
            rows=size.rows,
        )
        original_render(pt_app, layout, is_done)
        output.disable_autowrap()

    def _apply_resize() -> None:
        renderer._min_available_height = 0
        if getattr(app, "_running_in_terminal", False):
            app._redraw()
            return
        output.disable_autowrap()
        if rerender_banner is not None and rerender_banner():
            # Full chrome reset: clear + static banner, then redraw the live
            # region only. Do not call original erase — it leaves ghosts.
            renderer._last_screen = None
            renderer.reset(leave_alternate_screen=False)
            app._request_absolute_cursor_position()
            app._redraw()
            output.disable_autowrap()
            return
        # Keep the hardware cursor at prompt-toolkit's logical input position
        # while VTE reflows. Once dimensions settle, measure how the old frame
        # wraps at the new width and move to its top for one bounded erase.
        if not supports_synchronized_output(output):
            original_on_resize()
        else:
            size = output.get_size()
            rows_above = _reflowed_rows_above_cursor(
                renderer,
                columns=max(1, size.columns),
            )
            cursor = getattr(renderer, "_cursor_pos", None)
            if rows_above is None or cursor is None:
                original_on_resize()
            else:
                with synchronized_output(output):
                    output.cursor_backward(int(cursor.x) % max(1, size.columns))
                    output.cursor_up(rows_above)
                    output.erase_down()
                    output.flush()
                    renderer.reset(leave_alternate_screen=False)
                    app._redraw()
        output.disable_autowrap()

    def _apply_pending_resize() -> None:
        nonlocal resize_handle
        resize_handle = None
        _apply_resize()

    def _on_resize() -> None:
        nonlocal resize_handle
        renderer._min_available_height = 0
        if getattr(app, "_running_in_terminal", False):
            app._redraw()
            return
        output.disable_autowrap()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _apply_resize()
            return
        if resize_handle is not None:
            resize_handle.cancel()
        resize_handle = loop.call_later(_RESIZE_SETTLE_SECONDS, _apply_pending_resize)

    renderer.report_absolute_cursor_row = report_absolute_cursor_row  # type: ignore[method-assign]
    renderer.render = _render  # type: ignore[method-assign, assignment]
    app._on_resize = _on_resize  # type: ignore[method-assign]
    output.disable_autowrap()


__all__ = [
    "clamp_live_region_min_height",
    "install_shrink_resize_guard",
    "live_region_height_cap",
    "prepare_live_region_height",
]

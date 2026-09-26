"""Keep the live prompt region compact; reset chrome cleanly on resize.

Root cause
----------
prompt-toolkit sizes a non-fullscreen Screen as::

    height = max(_min_available_height, last_height, preferred_height)

After CPR, ``_min_available_height`` is "rows below the cursor" (the rest of the
terminal under the launch banner). That tall Screen scrolls the banner away,
and ``last_height`` sticks so later paints stay hollow.

Partial ``erase`` on SIGWINCH cannot keep scrollback chrome and the live
region aligned — soft-wrap and reflow leave Auto/composer ghosts. While the
screen holds only the banner, the host therefore clears the viewport, reprints
the static banner, and redraws the prompt from a clean cursor position.

Once a turn is on screen the transcript above must survive, so the host
erases only the live region before redrawing. Two things make that erase miss.

A width shrink reflows each old prompt row onto multiple physical rows — a
terminal rewraps a row that fills the width even when it was painted with
autowrap off — so prompt-toolkit's stored cursor offset is too small and
erasing from it strands the top of the frame.

Dragging a window edge then delivers a *burst* of resize signals, and the
repaint between them is gated on a cursor-position report. The first signal
erases the frame and resets the cursor and last screen; a second erase has no
frame to measure, falls back to prompt-toolkit's own path, and erases from
that reset cursor — far below the frame still on screen, which it leaves
behind. Either way the result is one status/composer copy per signal, so the
host erases exactly once per painted frame.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.output.base import Size

from surfaces.interactive_shell.ui.input_prompt.synchronized import synchronized_output

# Soft-wrap headroom above preferred Auto + composer. Keep tiny — blank Screen
# rows below the composer become a hollow band and invite ghost stacking.
_LIVE_REGION_HEIGHT_PAD = 1
# Absolute ceiling; never paint a live Screen taller than this.
_LIVE_REGION_HARD_MAX = 12


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


def _screen_row_width(screen: Any, row: int) -> int:
    """Cells of *row* a terminal will reflow, ignoring trailing blanks.

    prompt-toolkit pads a frame row out to the region width, but a terminal
    reflows a row by its content: a row of trailing spaces stays one physical
    row however far the window shrinks (verified against tmux). Counting that
    padding predicts rows the shrink never created, and the erase then starts
    above the frame and takes transcript with it.
    """
    data = getattr(screen, "data_buffer", {}).get(row, {})
    last = -1
    for column, char in data.items():
        text = getattr(char, "char", char)
        if isinstance(text, str) and text.strip():
            last = max(last, column)
    return last + 1


def _reflowed_rows_above_cursor(renderer: Any, *, columns: int) -> int | None:
    """Return the physical row offset after the previous frame reflows."""
    screen = getattr(renderer, "_last_screen", None)
    cursor = getattr(renderer, "_cursor_pos", None)
    if screen is None or cursor is None:
        return None
    rows = int(cursor.x) // columns
    for row in range(cursor.y):
        width = _screen_row_width(screen, row)
        rows += max(1, (width + columns - 1) // columns)
    return rows


def _erase_reflowed_live_region(renderer: Any, output: Any) -> bool:
    """Erase the prior prompt frame from its top row after terminal reflow.

    Returns False when the frame cannot be measured, in which case the caller
    must leave the screen alone: erasing from an unknown offset would cut into
    transcript scrollback.
    """
    columns = max(1, output.get_size().columns)
    rows_above = _reflowed_rows_above_cursor(renderer, columns=columns)
    cursor = getattr(renderer, "_cursor_pos", None)
    if rows_above is None or cursor is None:
        return False
    output.cursor_backward(int(cursor.x) % columns)
    output.cursor_up(rows_above)
    output.erase_down()
    output.reset_attributes()
    output.enable_autowrap()
    output.flush()
    renderer.reset(leave_alternate_screen=False)
    return True


def install_shrink_resize_guard(
    app: Application[Any],
    *,
    rerender_banner: Callable[[], bool] | None = None,
) -> None:
    """Install height + resize chrome guards for banner-safe layout.

    ``rerender_banner`` clears the viewport and reprints the static launch
    banner at the new size, returning True when it did so. Then resize skips
    prompt-toolkit's partial erase (which stacks Auto/composer ghosts) and
    redraws the live region from the cursor below the fresh banner. When it
    returns False, a turn is on screen and the reflowed live region is erased
    from its recalculated top row without touching transcript scrollback.
    """
    output = app.output
    renderer = app.renderer
    original_render = renderer.render
    original_report = renderer.report_absolute_cursor_row
    # Whether a live-region frame is on screen and therefore erasable. Only the
    # first signal of a resize burst has one; see the module docstring.
    painted = False

    def report_absolute_cursor_row(row: int) -> None:
        original_report(row)
        renderer._min_available_height = 0

    def _render(pt_app: Any, layout: Layout, is_done: bool = False) -> None:
        nonlocal painted
        size = output.get_size()
        if _size_changed(getattr(renderer, "_last_size", None), size):
            renderer._last_screen = None
            renderer._min_available_height = 0
        prepare_live_region_height(
            renderer,
            layout,
            columns=size.columns,
            rows=size.rows,
        )
        original_render(pt_app, layout, is_done)
        # ``is_done`` hands the rows to scrollback, leaving nothing to erase.
        painted = not is_done
        output.disable_autowrap()

    def _on_resize() -> None:
        nonlocal painted
        output.disable_autowrap()
        renderer._min_available_height = 0
        if rerender_banner is not None and rerender_banner():
            # Full chrome reset: clear + static banner, then redraw the live
            # region only. Do not call original erase — it leaves ghosts.
            renderer._last_screen = None
            renderer.reset(leave_alternate_screen=False)
            painted = False
            with synchronized_output(output):
                app._request_absolute_cursor_position()
                app._redraw()
            output.disable_autowrap()
            return
        # A turn is on screen. Width changes reflow the old prompt rows, so
        # erase from their recalculated top instead of the stale cursor row —
        # and only while a frame is painted, so the rest of a resize burst
        # cannot erase from the cursor that first erase already reset.
        # Erase and redraw inside one synchronized frame, so the gap between
        # them never reaches the screen as a flicker of the Auto bar and box.
        with synchronized_output(output):
            if painted and _erase_reflowed_live_region(renderer, output):
                painted = False
            app._request_absolute_cursor_position()
            app._redraw()
        output.disable_autowrap()

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

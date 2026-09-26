"""Keep the live prompt region anchored while terminal dimensions change.

Invariants the resize path depends on:

* prompt-toolkit sizes a non-fullscreen Screen as ``max(_min_available_height,
  last_height, preferred_height)``. After CPR ``_min_available_height`` is the
  rows below the cursor, so it is forced to zero on every paint; left alone it
  sizes a Screen tall enough to scroll the banner away, and ``last_height``
  then keeps later paints hollow.
* VTE anchors width reflow around the hardware cursor. Leaving that cursor in
  the composer pushes transcript rows into scrollback before SIGWINCH reaches
  the application. Between paints the hardware cursor therefore stays parked
  at the live region's top row; the composer renders its own cursor glyph.
* Any prompt-toolkit paint or erase first restores the physical cursor to the
  logical buffer position. Resize is the exception: from the parked top row it
  can erase and repaint the entire live region without touching transcript.
* The transcript above a turn is scrollback and must survive every erase.
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
    """Install height + resize chrome guards for banner-safe layout.

    ``rerender_banner`` clears the viewport and reprints the static launch
    banner at the new size, returning True when it did so. Otherwise resize
    erases from the cursor parked at the live region's top row, preserving the
    transcript above it.
    """
    output = app.output
    renderer = app.renderer
    original_render = renderer.render
    original_erase = renderer.erase
    original_report = renderer.report_absolute_cursor_row
    original_on_resize = app._on_resize
    painted = False
    parked = False
    frame_active = False

    def _restore_cursor() -> None:
        """Move from the live-region anchor to prompt-toolkit's logical cursor."""
        nonlocal parked
        if not parked:
            return
        columns = max(1, output.get_size().columns)
        rows_above = _reflowed_rows_above_cursor(renderer, columns=columns)
        cursor = getattr(renderer, "_cursor_pos", None)
        if rows_above is None or cursor is None:
            _erase_from_anchor(leave_alternate_screen=False)
            return
        output.cursor_down(rows_above)
        output.cursor_forward(int(cursor.x) % columns)
        output.flush()
        parked = False

    def _park_cursor() -> None:
        """Move the hidden hardware cursor to the live region's stable top row."""
        nonlocal parked
        screen = getattr(renderer, "_last_screen", None)
        if screen is None or bool(getattr(screen, "show_cursor", True)):
            return
        columns = max(1, output.get_size().columns)
        rows_above = _reflowed_rows_above_cursor(renderer, columns=columns)
        cursor = getattr(renderer, "_cursor_pos", None)
        if rows_above is None or cursor is None:
            return
        output.cursor_backward(int(cursor.x) % columns)
        output.cursor_up(rows_above)
        output.hide_cursor()
        output.flush()
        parked = True

    def _erase_from_anchor(*, leave_alternate_screen: bool = True) -> None:
        """Erase the painted live region without moving into transcript rows."""
        nonlocal painted, parked
        output.erase_down()
        output.reset_attributes()
        output.enable_autowrap()
        output.flush()
        renderer.reset(leave_alternate_screen=leave_alternate_screen)
        painted = False
        parked = False

    def report_absolute_cursor_row(row: int) -> None:
        original_report(row)
        renderer._min_available_height = 0

    def _render(pt_app: Any, layout: Layout, is_done: bool = False) -> None:
        nonlocal frame_active, painted
        framed = not frame_active and not getattr(app, "_running_in_terminal", False)
        if framed:
            frame_active = True
        try:
            with synchronized_output(output, enabled=framed):
                # prompt-toolkit flushes at the end of ``render``. Hold that
                # flush until the following park movement is buffered too, so
                # the terminal never observes a painted frame with its cursor
                # still in the composer (and cannot anchor a concurrent reflow
                # there).
                real_flush = output.flush
                output.flush = lambda: None  # type: ignore[method-assign]
                try:
                    size = output.get_size()
                    size_changed = _size_changed(getattr(renderer, "_last_size", None), size)
                    if parked and size_changed:
                        _erase_from_anchor(leave_alternate_screen=False)
                    else:
                        _restore_cursor()
                    prepare_live_region_height(
                        renderer,
                        layout,
                        columns=size.columns,
                        rows=size.rows,
                    )
                    original_render(pt_app, layout, is_done)
                    painted = not is_done
                    if painted:
                        _park_cursor()
                finally:
                    output.flush = real_flush  # type: ignore[method-assign]
                    real_flush()
        finally:
            if framed:
                frame_active = False

    def _erase(leave_alternate_screen: bool = True) -> None:
        nonlocal painted, parked
        if parked:
            _erase_from_anchor(leave_alternate_screen=leave_alternate_screen)
            return
        original_erase(leave_alternate_screen=leave_alternate_screen)
        painted = False
        parked = False

    def _on_resize() -> None:
        nonlocal frame_active, painted, parked
        # Search/system controls still use prompt-toolkit's real cursor. Keep
        # its stock resize path while one of those transient controls is active.
        if painted and not parked:
            original_on_resize()
            return
        # ``_redraw`` paints synchronously unless the app is running something
        # in the terminal. The stdout proxy that does so drives this same
        # private mode, which does not nest — its end marker would present our
        # erase on its own — and the repaint would not land inside our frame
        # anyway. Both reasons say the same thing: do not open one.
        framed = not frame_active and not getattr(app, "_running_in_terminal", False)
        if framed:
            frame_active = True
        try:
            with synchronized_output(output, enabled=framed):
                output.disable_autowrap()
                renderer._min_available_height = 0
                if rerender_banner is not None and rerender_banner():
                    # Full chrome reset: clear + static banner, then redraw the
                    # live region only. Do not call prompt-toolkit's own erase
                    # here — it leaves ghosts.
                    renderer._last_screen = None
                    renderer.reset(leave_alternate_screen=False)
                    painted = False
                    parked = False
                else:
                    if painted and parked:
                        _erase_from_anchor(leave_alternate_screen=False)
                app._redraw()
        finally:
            if framed:
                frame_active = False
            output.disable_autowrap()

    renderer.report_absolute_cursor_row = report_absolute_cursor_row  # type: ignore[method-assign]
    renderer.render = _render  # type: ignore[method-assign, assignment]
    renderer.erase = _erase  # type: ignore[method-assign]
    app._on_resize = _on_resize  # type: ignore[method-assign]
    output.disable_autowrap()


__all__ = [
    "clamp_live_region_min_height",
    "install_shrink_resize_guard",
    "live_region_height_cap",
    "prepare_live_region_height",
]

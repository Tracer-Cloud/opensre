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
from unicodedata import category

from prompt_toolkit.application import Application
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.layout.screen import Screen
from prompt_toolkit.output.base import Size
from prompt_toolkit.utils import get_cwidth

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

    A cursor-position report can leave a hollow screen much taller than the
    chrome. Drop that. A live plan is often taller than the hard max on
    purpose; forgetting a screen that tall forces a full repaint, and the
    repaint reserves its rows with newlines that scroll the checklist header
    into the transcript.

    Returns the live-region cap applied (for tests).
    """
    preferred = layout.container.preferred_height(columns, rows).preferred
    cap = live_region_height_cap(preferred)
    renderer._min_available_height = 0
    last = getattr(renderer, "_last_screen", None)
    last_height = int(getattr(last, "height", 0) or 0)
    if last is not None and last_height > max(cap, preferred):
        renderer._last_screen = None
    return cap


# Back-compat name used by older tests / imports.
clamp_live_region_min_height = prepare_live_region_height


def _size_changed(previous: Size | None, current: Size) -> bool:
    if previous is None:
        return False
    return previous.rows != current.rows or previous.columns != current.columns


def _raw_terminal_text(text: str) -> str:
    """Normalize line endings for direct terminal output."""
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def _terminal_safe_text(text: str) -> str:
    """Replace terminal control characters with visible, inert glyphs."""
    safe: list[str] = []
    for char in text:
        codepoint = ord(char)
        if codepoint < 0x20:
            safe.append(chr(0x2400 + codepoint))
        elif codepoint == 0x7F:
            safe.append("␡")
        elif category(char) == "Cc":
            safe.append("�")
        else:
            safe.append(char)
    return "".join(safe)


def _display_clusters(text: str) -> list[str]:
    """Group characters that must stay together when clipping terminal text."""
    clusters: list[str] = []
    for char in text:
        codepoint = ord(char)
        joins_previous = bool(
            clusters
            and (
                get_cwidth(char) == 0
                or clusters[-1].endswith("\u200d")
                or 0x1F3FB <= codepoint <= 0x1F3FF
                or (
                    0x1F1E6 <= codepoint <= 0x1F1FF
                    and len(clusters[-1]) == 1
                    and 0x1F1E6 <= ord(clusters[-1]) <= 0x1F1FF
                )
            )
        )
        if joins_previous:
            clusters[-1] += char
        elif get_cwidth(char) == 0:
            clusters.append(f"◌{char}")
        else:
            clusters.append(char)
    return clusters


def _cluster_width(cluster: str) -> int:
    """Return the display width of one grapheme-like terminal cluster."""
    return max((get_cwidth(char) for char in cluster), default=0)


def _prefix_clusters_within_width(clusters: list[str], width: int) -> tuple[str, int]:
    selected: list[str] = []
    cells = 0
    for cluster in clusters:
        cluster_width = _cluster_width(cluster)
        if cells + cluster_width > width:
            break
        selected.append(cluster)
        cells += cluster_width
    return "".join(selected), cells


def _suffix_clusters_within_width(clusters: list[str], width: int) -> tuple[str, int]:
    selected: list[str] = []
    cells = 0
    for cluster in reversed(clusters):
        cluster_width = _cluster_width(cluster)
        if cells + cluster_width > width:
            break
        selected.append(cluster)
        cells += cluster_width
    return "".join(reversed(selected)), cells


def _tail_within_width(text: str, width: int) -> str:
    """Return the longest suffix of ``text`` that fits ``width`` cells."""
    if width <= 0:
        return ""
    suffix, _cells = _suffix_clusters_within_width(_display_clusters(text), width)
    return suffix


def _compact_input_window(before_cursor: str, after_cursor: str, width: int) -> str:
    """Return a safe, clipped editor window with text around the caret."""
    before = _display_clusters(_terminal_safe_text(before_cursor))
    after = _display_clusters(_terminal_safe_text(after_cursor))
    right, right_width = _prefix_clusters_within_width(after, width // 2)
    left, left_width = _suffix_clusters_within_width(before, width - right_width)
    right, _right_width = _prefix_clusters_within_width(after, width - left_width)
    return f"{left}▌{right}"


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
            cell_width = max(int(getattr(char, "width", 1) or 0), 1)
            last = max(last, column + cell_width - 1)
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
    rerender_banner: Callable[[], str | None] | None = None,
) -> None:
    """Install compact-height and settled-resize guards for the live prompt.

    ``rerender_banner`` returns the static launch banner at the new size only
    while the shell is empty. The resize transaction writes it synchronously
    with the live prompt; once transcript exists, only the measured live region
    is erased and repainted.
    """
    output = app.output
    renderer = app.renderer
    original_on_resize = app._on_resize
    original_render = renderer.render
    original_report = renderer.report_absolute_cursor_row
    resize_handle: asyncio.TimerHandle | None = None
    resize_deferred = False
    deferred_cursor_wrap_rows: int | None = None
    # Set once the live region has been painted. A later reset (stdout inserted
    # above the prompt) clears ``_last_screen``; the next paint must not treat
    # that as the first frame and reserve its full height with newlines.
    prompt_painted = False

    def report_absolute_cursor_row(row: int) -> None:
        original_report(row)
        renderer._min_available_height = 0

    def _render_deferred_input(pt_app: Any) -> None:
        """Show buffer edits on the cursor row while full chrome cannot fit."""
        nonlocal deferred_cursor_wrap_rows
        cursor = getattr(renderer, "_cursor_pos", None)
        if cursor is None:
            return
        columns = max(1, output.get_size().columns)
        caret = "▌"
        line_width = max(0, columns - 1)
        label = "> " if line_width >= get_cwidth("> ") + get_cwidth(caret) else ""
        content_width = max(0, line_width - get_cwidth(label) - get_cwidth(caret))
        document = pt_app.current_buffer.document
        editor = _compact_input_window(
            document.current_line_before_cursor,
            document.current_line_after_cursor,
            content_width,
        )
        compact_line = f"{label}{editor}" if line_width else ""
        with synchronized_output(output):
            output.write_raw("\r")
            output.erase_end_of_line()
            output.write(compact_line)
            # Keep the hardware cursor at prompt-toolkit's last logical
            # position so a later terminal reflow has a stable vertical anchor.
            output.write_raw("\r")
            output.cursor_forward(int(cursor.x) % columns)
            output.flush()
        deferred_cursor_wrap_rows = int(cursor.x) // columns
        output.disable_autowrap()

    def _render(pt_app: Any, layout: Layout, is_done: bool = False) -> None:
        nonlocal deferred_cursor_wrap_rows, resize_deferred, resize_handle, prompt_painted
        if is_done and resize_handle is not None:
            resize_handle.cancel()
            resize_handle = None
        if is_done:
            if resize_deferred:
                _render_deferred_input(pt_app)
                resize_deferred = False
                deferred_cursor_wrap_rows = None
                output.write_raw("\r\n")
                output.reset_attributes()
                output.enable_autowrap()
                output.flush()
                return
            resize_deferred = False
            deferred_cursor_wrap_rows = None
        elif resize_deferred:
            _render_deferred_input(pt_app)
            return
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
        # ``run_in_terminal`` resets the renderer before redrawing. With no
        # previous screen, prompt-toolkit reserves the new height by writing
        # newlines after the frame. At the bottom of the viewport those
        # newlines scroll the plan header into scrollback, then the next
        # paint draws it again. A blank screen of the chrome's own height
        # skips that reservation; every cell is still drawn because the
        # blank has no characters. The first paint is left alone so the
        # terminal still scrolls once to make room for the composer.
        if (
            not is_done
            and prompt_painted
            and getattr(renderer, "_last_screen", None) is None
            and getattr(renderer, "_last_size", None) is None
        ):
            preferred = layout.container.preferred_height(size.columns, size.rows).preferred
            blank_height = min(max(preferred, 1), size.rows)
            renderer._last_screen = Screen(initial_height=blank_height)
            renderer._last_size = size
        original_render(pt_app, layout, is_done)
        prompt_painted = True
        if is_done:
            output.enable_autowrap()
            output.flush()
        else:
            output.disable_autowrap()

    def _apply_resize() -> None:
        nonlocal deferred_cursor_wrap_rows, resize_deferred
        renderer._min_available_height = 0
        if getattr(app, "_running_in_terminal", False):
            app._redraw()
            return
        output.disable_autowrap()
        banner = rerender_banner() if rerender_banner is not None else None
        if banner is not None:
            resize_deferred = False
            deferred_cursor_wrap_rows = None
            # Full chrome reset: clear + static banner + live region are one
            # terminal transaction. Writing via app.output avoids patched
            # stdout scheduling each piece as a separate run_in_terminal task.
            with synchronized_output(output):
                output.erase_screen()
                output.cursor_goto(0, 0)
                output.write_raw(_raw_terminal_text(banner))
                output.flush()
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
            resize_deferred = False
            deferred_cursor_wrap_rows = None
            original_on_resize()
        else:
            size = output.get_size()
            rows_above = _reflowed_rows_above_cursor(
                renderer,
                columns=max(1, size.columns),
            )
            cursor = getattr(renderer, "_cursor_pos", None)
            if rows_above is None or cursor is None:
                resize_deferred = False
                deferred_cursor_wrap_rows = None
                original_on_resize()
            else:
                cursor_wrap_rows = int(cursor.x) // max(1, size.columns)
                if resize_deferred and deferred_cursor_wrap_rows is not None:
                    # Direct compact output turns the cursor's soft-wrapped
                    # continuation into an independent terminal row. Preserve
                    # that physical offset when a wider viewport would
                    # otherwise collapse the logical prompt row again.
                    rows_above = max(
                        0,
                        rows_above + deferred_cursor_wrap_rows - cursor_wrap_rows,
                    )
            if rows_above is not None and cursor is not None and rows_above >= size.rows:
                # The old live-region top has moved above the addressable
                # viewport. Leave the full frame untouched and show edits on a
                # compact cursor-row fallback until a later resize makes the
                # whole region reachable again; erasing here would strand old
                # prompt chrome inside transcript scrollback.
                resize_deferred = True
                return
            if rows_above is not None and cursor is not None:
                resize_deferred = False
                deferred_cursor_wrap_rows = None
                with synchronized_output(output):
                    # Reflow can change the terminal's real column independently
                    # of renderer._cursor_pos. A carriage return is the only
                    # reliable way to anchor the erase at column zero.
                    output.write_raw("\r")
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

    original_erase = renderer.erase

    def erase(leave_alternate_screen: bool = True) -> None:
        """Clear the painted live region, including the border under the cursor.

        prompt-toolkit walks up from the input cursor. That cursor sits on the
        line inside the composer, one row above the bottom border. A terminal
        that left the hardware cursor on the border then stops one row short
        and the plan header stays in the viewport; the next line scrolled in
        above the prompt commits it to scrollback.
        """
        screen = getattr(renderer, "_last_screen", None)
        cursor = getattr(renderer, "_cursor_pos", None)
        columns = max(1, output.get_size().columns)
        rows_above = _reflowed_rows_above_cursor(renderer, columns=columns)
        if (
            screen is None
            or cursor is None
            or rows_above is None
            or getattr(renderer, "full_screen", False)
        ):
            original_erase(leave_alternate_screen=leave_alternate_screen)
            return
        below = max(0, int(getattr(screen, "height", 0) or 0) - 1 - int(cursor.y))
        # Cursor-down does not scroll. From the input row it lands on the
        # border; if the hardware cursor is already there, it stays put when
        # that border is the last terminal row, and the longer cursor-up
        # still reaches the top of the frame.
        if below:
            output.cursor_down(below)
        output.write_raw("\r")
        output.cursor_up(rows_above + below)
        output.erase_down()
        output.reset_attributes()
        output.enable_autowrap()
        output.flush()
        renderer.reset(leave_alternate_screen=leave_alternate_screen)

    renderer.report_absolute_cursor_row = report_absolute_cursor_row  # type: ignore[method-assign]
    renderer.render = _render  # type: ignore[method-assign, assignment]
    renderer.erase = erase  # type: ignore[method-assign]
    app._on_resize = _on_resize  # type: ignore[method-assign]
    output.disable_autowrap()


__all__ = [
    "clamp_live_region_min_height",
    "install_shrink_resize_guard",
    "live_region_height_cap",
    "prepare_live_region_height",
]

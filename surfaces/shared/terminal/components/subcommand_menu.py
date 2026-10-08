"""Composer-tray-styled picker for a slash command's subcommands.

Serves the window where ``CommandTrayControl`` cannot: a dispatching command
holds stdin exclusively, so the prompt application is suspended and there is no
live buffer to render a completion tray from. Chrome mirrors that control and
``rounded_composer_frame`` row for row (border, header, blank, options, blank,
hint, border); keep the two in step so the same catalog reads the same way on
both surfaces.

Every painted row is exactly ``menu_columns()`` wide — one column short of the
TTY — or DEC autowrap reflows the block on the next resize.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from shutil import get_terminal_size

import infrastructure.terminal.theme as ui_theme
from infrastructure.safety.terminal_output import strip_terminal_controls
from surfaces.shared.terminal.components.choice_menu import (
    enter_inline_menu,
    erase_menu_lines,
    leave_inline_menu,
    menu_columns,
    read_menu_action,
    repl_tty_interactive,
    write_menu_line,
)
from surfaces.shared.terminal.prompt_layout import clip_prompt_text, prompt_text_width

# The composer tray's own bounds, so both surfaces scroll at the same point.
MAX_VISIBLE_SUBCOMMANDS = 6
# Below this width the description column is dropped rather than wrapped.
_DESCRIPTION_MIN_WIDTH = 60
_MAX_NAME_WIDTH = 24
_HINT = "↑↓ navigate   Enter select   Esc close"
_NARROW_HINT = "↑↓ move  Enter select  Esc close"
_NARROW_HINT_WIDTH = 44
# Top border, header, blank, options, blank, hint, bottom border.
_CHROME_ROWS = 6
# The frame's two border columns plus a column of air on each side.
_FRAME_COLUMNS = 4
# Chrome, one option row, and the spare row ``erase_menu_lines`` needs to delete
# the block instead of clearing it in place.
MIN_MENU_HEIGHT = _CHROME_ROWS + 2
MIN_MENU_WIDTH = 24


def _border_style() -> str:
    return f"{ui_theme.INPUT_SURFACE_BG_ANSI}{ui_theme.SECONDARY_ANSI}"


def _rule(left: str, right: str, width: int) -> str:
    inner = max(0, width - 2)
    return f"{_border_style()}{left}{'─' * inner}{right}{ui_theme.ANSI_RESET}"


def _framed(text: str, style: str, width: int) -> str:
    """One tray row: ``text`` on the composer plate between the frame's edges."""
    inner = max(0, width - 2)
    content = " " + clip_prompt_text(text, max(0, inner - 1))
    return _framed_runs([(style, content)], prompt_text_width(content), width)


def _framed_runs(runs: list[tuple[str, str]], used: int, width: int) -> str:
    """Frame pre-styled runs, padding the plate out to the right edge.

    Rows that style the name and its description differently cannot go through
    a single style string, so the caller hands over the runs and the visible
    width it already spent.
    """
    inner = max(0, width - 2)
    tail = runs[-1][0] if runs else ui_theme.INPUT_SURFACE_BG_ANSI
    body = "".join(f"{style}{text}{ui_theme.ANSI_RESET}" for style, text in runs)
    padding = f"{tail}{' ' * max(0, inner - used)}{ui_theme.ANSI_RESET}"
    edge = f"{_border_style()}│{ui_theme.ANSI_RESET}"
    return f"{edge}{body}{padding}{edge}"


def _name_width(options: Sequence[tuple[str, str]], width: int) -> int:
    longest = max((prompt_text_width(name) for name, _meta in options), default=0)
    return min(_MAX_NAME_WIDTH, longest, max(1, width - _FRAME_COLUMNS - 2))


def _header_row(parent: str, selected: int, total: int, width: int) -> str:
    title = f"Subcommands · {parent}" if parent else "Subcommands"
    counter = f"{selected + 1} / {total}"
    gap = " " * max(
        1, width - _FRAME_COLUMNS - prompt_text_width(title) - prompt_text_width(counter)
    )
    return _framed(f"{title}{gap}{counter}", _border_style(), width)


def _option_row(
    name: str,
    meta: str,
    *,
    selected: bool,
    name_width: int,
    width: int,
) -> str:
    background = ui_theme.menu_selection_bg_ansi() if selected else ui_theme.INPUT_SURFACE_BG_ANSI
    name_style = (
        f"{background}\x1b[1m{ui_theme.HIGHLIGHT_ANSI}"
        if selected
        else f"{background}{ui_theme.TEXT_ANSI}"
    )
    # The focused row's description stays unbolded body text, like the tray's
    # ``command-tray.current.description``; an unfocused one is secondary.
    meta_style = (
        f"{background}\x1b[22m{ui_theme.TEXT_ANSI}"
        if selected
        else f"{background}{ui_theme.SECONDARY_ANSI}"
    )
    marker = "› " if selected else "  "
    label = clip_prompt_text(name, name_width)
    head = f" {marker}{label}"
    runs = [(name_style, head)]
    used = prompt_text_width(head)
    if width >= _DESCRIPTION_MIN_WIDTH and meta:
        gap = " " * (name_width - prompt_text_width(label) + 2)
        body = clip_prompt_text(meta, max(0, width - _FRAME_COLUMNS - used - len(gap)))
        runs.append((meta_style, gap + body))
        used += len(gap) + prompt_text_width(body)
    return _framed_runs(runs, used, width)


def _hint_row(*, more: bool, width: int) -> str:
    hint = _HINT if width >= _NARROW_HINT_WIDTH else _NARROW_HINT
    if more and width >= _DESCRIPTION_MIN_WIDTH:
        hint += " " * max(2, width - _FRAME_COLUMNS - prompt_text_width(hint) - 6) + "↓ more"
    return _framed(hint, _border_style(), width)


def _visible_rows(total: int, height: int) -> int:
    return max(1, min(MAX_VISIBLE_SUBCOMMANDS, total, height - _CHROME_ROWS - 1))


def too_small(width: int, height: int) -> bool:
    """True when the frame cannot be painted and later deleted in full.

    ``erase_menu_lines`` can only delete a block of at most ``lines - 1`` rows;
    a taller one is cleared in place and leaves fragments behind. The smallest
    frame is the chrome plus one option, so it needs one row more than that.
    """
    return width < MIN_MENU_WIDTH or height < MIN_MENU_HEIGHT


def _notice_rows(width: int, height: int) -> list[str]:
    notice = (
        "Esc close",
        f"Resize to at least {MIN_MENU_WIDTH}×{MIN_MENU_HEIGHT}",
        "to pick a subcommand.",
    )
    return [
        f"{ui_theme.DIM_COUNTER_ANSI}{clip_prompt_text(text, width)}{ui_theme.ANSI_RESET}"
        for text in notice[: max(0, height - 1)]
    ]


@dataclass(frozen=True)
class _Painted:
    """What the last frame actually put on screen.

    ``showed_options`` gates selection: the terminal can be resized between a
    paint and the keypress that follows it, so a key must be judged against the
    frame the user was looking at, never against a freshly measured size.
    """

    height: int
    top: int
    showed_options: bool


def _draw(
    parent: str,
    options: Sequence[tuple[str, str]],
    *,
    selected: int,
    top: int,
    erase_lines: int,
) -> _Painted:
    """Paint one frame and report its height, window top, and what it showed."""
    width = menu_columns()
    height = get_terminal_size(fallback=(80, 24)).lines
    if erase_lines:
        erase_menu_lines(erase_lines)
    if too_small(width, height):
        rows_painted = _notice_rows(width, height)
        for row in rows_painted:
            write_menu_line(row)
        return _Painted(len(rows_painted), top, showed_options=False)

    rows = _visible_rows(len(options), height)
    top = max(0, min(top, len(options) - rows, selected))
    top = max(top, selected - rows + 1)
    name_width = _name_width(options, width)

    write_menu_line(_rule("╭", "╮", width))
    write_menu_line(_header_row(parent, selected, len(options), width))
    write_menu_line(_framed("", ui_theme.INPUT_SURFACE_BG_ANSI, width))
    for index in range(top, top + rows):
        name, meta = options[index]
        write_menu_line(
            _option_row(
                name,
                meta,
                selected=index == selected,
                name_width=name_width,
                width=width,
            )
        )
    write_menu_line(_framed("", ui_theme.INPUT_SURFACE_BG_ANSI, width))
    write_menu_line(_hint_row(more=top + rows < len(options), width=width))
    write_menu_line(_rule("╰", "╯", width))
    return _Painted(_CHROME_ROWS + rows, top, showed_options=True)


def repl_choose_subcommand(
    *,
    parent: str,
    options: Sequence[tuple[str, str]],
    initial_value: str | None = None,
) -> str | None:
    """Pick one subcommand of ``parent``; return its name, or ``None`` on Esc.

    ``options`` is a command's ``first_arg_completions``: ``(name, description)``
    pairs. Pass the same constant the :class:`SlashCommand` registers so the tray
    and this picker can never drift apart. Only call when
    :func:`repl_tty_interactive` is True.
    """
    cleaned = [
        (strip_terminal_controls(name), strip_terminal_controls(meta))
        for name, meta in options
        if name.strip()
    ]
    if not cleaned or not repl_tty_interactive():
        return None
    selected = 0
    if initial_value is not None:
        for index, (name, _meta) in enumerate(cleaned):
            if name == initial_value:
                selected = index
                break
    top = 0
    drawn = 0
    enter_inline_menu()
    try:
        while True:
            painted = _draw(parent, cleaned, selected=selected, top=top, erase_lines=drawn)
            drawn, top = painted.height, painted.top
            action = read_menu_action()
            if action in ("cancel", "eof"):
                return None
            if not painted.showed_options:
                # The resize notice was on screen, so no row was selectable and
                # none is now: repaint at the current size before taking a key.
                continue
            if action == "up":
                selected = (selected - 1) % len(cleaned)
            elif action == "down":
                selected = (selected + 1) % len(cleaned)
            elif action == "enter":
                return cleaned[selected][0]
    finally:
        erase_menu_lines(drawn, delete=True)
        leave_inline_menu()


__all__ = [
    "MAX_VISIBLE_SUBCOMMANDS",
    "MIN_MENU_HEIGHT",
    "MIN_MENU_WIDTH",
    "repl_choose_subcommand",
    "too_small",
]

"""Interactive choice helpers for TTY-first REPL flows.

Inline menus render in the terminal scrollback (below the submitted command),
not as a separate prompt-toolkit full-screen dialog — important when the REPL
already runs under asyncio.

Each menu erases itself on exit (selection or Esc) so nested menus never
pile up — only the result output and the next level appear on screen.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from typing import Literal

from rich.console import Console
from rich.markup import escape

import infrastructure.terminal.theme as ui_theme
from infrastructure.safety.terminal_output import strip_terminal_controls
from surfaces.shared.terminal.components.key_reader import read_key_unix, read_key_windows

_HINT = "↑↓ Navigate • Enter/1-9 Select • Esc cancel"
_HINT_MULTI = "↑↓ Navigate • Space/Enter/1-9 Toggle • Submit to confirm • Esc cancel"


_HINT_NO_KEYS = "↑↓ Navigate • Enter Select • Esc cancel"


def _option_token(index: int, *, letter_keys: bool, numbered: bool) -> str:
    """Row prefix: ``(A)`` in letter mode, ``1.`` when numbered, else empty."""
    if letter_keys:
        return f"({chr(ord('A') + index)})"
    return f"{index + 1}." if numbered else ""


def _option_index_from_key(action: str, *, letter_keys: bool, numbered: bool) -> int | None:
    """Zero-based option index a select keystroke names, or ``None``."""
    if len(action) != 1:
        return None
    if letter_keys:
        return ord(action.upper()) - ord("A") if action.isalpha() else None
    if numbered:
        return int(action) - 1 if action.isdigit() else None
    return None


def _select_hint(count: int, *, letter_keys: bool, numbered: bool, multi_select: bool) -> str:
    """Key hint reflecting letter / numeric / no selectors and the option count."""
    if letter_keys:
        keys = f"A-{chr(ord('A') + count - 1)}" if count > 1 else "A"
        if multi_select:
            return f"↑↓ Navigate • Space/Enter/{keys} Toggle • Submit to confirm • Esc cancel"
        return f"↑↓ Navigate • Enter/{keys} Select • Esc cancel"
    if not numbered:
        return _HINT_NO_KEYS
    return _HINT_MULTI if multi_select else _HINT


# Airy block indent so the panel is not flush against the left margin.
_BLOCK_INDENT = "  "
_SUBMIT = "Submit"
_CHECKED = "[x]"
_UNCHECKED = "[ ]"
CRUMB_SEP = "  ›  "
# Tight Droid-style panel: no blank line above slash-command titles.
_MENU_LEADING_LINES = 0
# Headered menus (Ask User) need one blank above so the accent header reads as
# a new section after Plan complete / reply text, not a continuation line.
_HEADER_SECTION_GAP = 1
_TERMINAL_NEWLINE = "\r\n"
MenuAction = Literal["up", "down", "enter", "cancel", "eof", "ignore"]


def repl_tty_interactive() -> bool:
    """Return True when stdin/stdout support an interactive picker UI."""
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def ensure_tty_column_zero() -> None:
    """Reset the cursor column before Rich output when a TTY is active."""
    if repl_tty_interactive():
        reset_tty_column()


def prepare_repl_output_line() -> None:
    """Begin Rich output on a new line after inline menu I/O."""
    if repl_tty_interactive():
        sys.stdout.write(_TERMINAL_NEWLINE)
        reset_tty_column()


def repl_section_break(console: Console) -> None:
    """Blank line + dim rule between an inline menu step and Rich output."""
    prepare_repl_output_line()
    console.print()
    console.rule(characters="─", style=str(ui_theme.DIM))
    console.print()


# ── raw key reader ───────────────────────────────────────────────────────────


def _read_action(*, alpha_keys: bool = False) -> MenuAction:
    """Map a raw keypress to a menu action.

    Delegates terminal I/O to :mod:`key_reader` and applies
    choice_menu-specific overrides: Tab → ``"down"``,
    right-arrow → ``"enter"``, left-arrow → ``"ignore"``. With ``alpha_keys``
    an option letter (``A``/``B``/…) is returned verbatim for letter select.
    """
    key = (
        read_key_windows(alpha_keys=alpha_keys)
        if os.name == "nt"
        else read_key_unix(alpha_keys=alpha_keys)
    )
    if key == "tab":
        return "down"
    if key == "right":
        return "enter"
    if key == "left":
        return "ignore"
    return key  # type: ignore[return-value]


def read_menu_action() -> MenuAction:
    """Read one normalized inline-menu action from stdin."""
    return _read_action()


# ── rendering helpers ────────────────────────────────────────────────────────


def _cols() -> int:
    return max(1, shutil.get_terminal_size(fallback=(80, 24)).columns)


def _viewport_rows() -> int:
    return max(2, shutil.get_terminal_size(fallback=(80, 24)).lines)


def _menu_paint_width() -> int:
    """Columns for one physical menu row (last cell empty so DEC autowrap stays off)."""
    return max(1, _cols() - 1)


def menu_columns() -> int:
    """Return the inline-menu paint width (one column short of the TTY)."""
    return _menu_paint_width()


def _clip_to_row(text: str, width: int) -> str:
    """Keep ``text`` on one physical row so erase height matches the cursor."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return text[:1]
    return text[: width - 1] + "…"


def _write_option_row(*, prefix: str, label: str, width: int, selected: bool) -> None:
    """Accent only the content; pad with plain spaces (avoids a full-width bar)."""
    content = _clip_to_row(f"{_BLOCK_INDENT}{prefix} {label}", width)
    pad = max(0, width - len(content))
    style = ui_theme.PROMPT_ACCENT_ANSI if selected else ui_theme.DIM_COUNTER_ANSI
    write_menu_line(f"{style}{content}{ui_theme.ANSI_RESET}{' ' * pad}")


def _sanitize_menu(
    title: str,
    crumb: str,
    labels: list[str],
) -> tuple[str, str, list[str]]:
    """Strip model-supplied controls before raw ANSI write or row counting."""
    return (
        strip_terminal_controls(title),
        strip_terminal_controls(crumb),
        [strip_terminal_controls(label) for label in labels],
    )


def _wrap_note(note: str, width: int) -> tuple[str, ...]:
    """Physical rows for a dim note; empty when there is nothing to say."""
    text = strip_terminal_controls(note).strip()
    if not text:
        return ()
    inner = max(1, width - len(_BLOCK_INDENT))
    return tuple(textwrap.wrap(text, width=inner))


def _menu_height(
    crumb: str,
    labels: list[str],
    *,
    multi_select: bool = False,
    header: str = "",
    note: str = "",
) -> int:
    # [gap], [header], title, [note…], [crumb], blank, choices, [Submit], blank, hint
    submit = 1 if multi_select else 0
    gap = _HEADER_SECTION_GAP if header else 0
    lead = _MENU_LEADING_LINES + gap + (1 if header else 0)
    notes = len(_wrap_note(note, _menu_paint_width()))
    return lead + 1 + notes + (1 if crumb else 0) + 1 + len(labels) + submit + 1 + 1


def write_menu_line(text: str = "") -> None:
    """Write one inline-menu line at column zero even while the terminal is in raw mode."""
    if text:
        sys.stdout.write(f"\r{text}{_TERMINAL_NEWLINE}")
        return
    sys.stdout.write(_TERMINAL_NEWLINE)


def _erase_menu_block(height: int, *, delete: bool = False) -> None:
    """Rewind the inline menu. Clear in place for redraw; delete rows on leave.

    ``ESC[J`` blanks the menu but leaves the rows in the scrollback — after a
    8–10 line Ask User picker that hole sits between the reply and the ✓ recap
    (and again above Thinking). ``CSI n M`` removes the rows so the transcript
    closes up.

    Delete-line is only safe when the whole block is still on screen. ``CSI n A``
    stops at the top of the viewport, so a taller block would delete transcript
    rows above the menu or leave fragments. Overflow climbs as far as the
    viewport allows and clears in place.
    """
    if height:
        rows = _viewport_rows()
        climb = min(height, rows - 1)
        if delete and height <= rows - 1:
            sys.stdout.write(f"\r\x1b[{climb}A\r\x1b[{climb}M")
        else:
            sys.stdout.write(f"\r\x1b[{climb}A\r\x1b[J")
    reset_tty_column()


def reset_tty_column() -> None:
    """Return the cursor to column zero after inline menu I/O.

    Menu rows are padded to the terminal width, so the cursor often ends on a
    high column. Rich output that follows must start at column zero or tables
    render as a diagonal block of leading whitespace.
    """
    sys.stdout.write("\r")
    sys.stdout.flush()


def hide_terminal_cursor() -> None:
    """Hide the hardware cursor while an inline menu owns the screen.

    Arrow-key menus draw their own selection marker; the parked hardware cursor
    would otherwise sit on its own row below the hint as a stray block.
    """
    if repl_tty_interactive():
        sys.stdout.write("\x1b[?25l")
        sys.stdout.flush()


def show_terminal_cursor() -> None:
    """Restore the hardware cursor after an inline menu exits."""
    if repl_tty_interactive():
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


def leave_inline_menu() -> None:
    """Restore cooked stdin and park the cursor at column zero.

    Pair with :func:`hide_terminal_cursor` in a ``finally`` so select, Esc,
    and exceptions all recook stdin. Without this, padded menu rows leave the
    cursor mid-line and an inherited non-canonical or no-echo TTY breaks later
    shell input.

    Also drain pending CSI (arrow keys / CPR) so they cannot leak as
    ``^[[A`` into the transcript once the menu releases the keyboard.
    """
    from surfaces.shared.terminal.components.cpr_stdin import drain_stale_cpr_bytes
    from surfaces.shared.terminal.components.key_reader import (
        flush_pending_input,
        restore_stdin_terminal,
    )

    show_terminal_cursor()
    restore_stdin_terminal()
    flush_pending_input()
    drain_stale_cpr_bytes()
    # Column zero only — a newline here is a second blank after the reply
    # (the stream already printed one) and after a deleted menu.
    reset_tty_column()


def erase_menu_lines(height: int, *, delete: bool = False) -> None:
    """Erase a previously-rendered inline menu block.

    ``delete=False`` (redraw) clears in place. ``delete=True`` (leave) removes
    the rows so they do not remain as a hole in the transcript.
    """
    _erase_menu_block(height, delete=delete)


def _clear_prompt_toolkit_paint() -> None:
    """Drop any live prompt-toolkit frame so option menus own the screen alone."""
    from contextlib import suppress

    try:
        from prompt_toolkit.application.current import get_app_or_none
    except ImportError:
        return
    app = get_app_or_none()
    if app is None:
        return
    renderer = getattr(app, "renderer", None)
    if renderer is not None:
        # Erase only the app's reserved rows so the transcript stays and the
        # menu draws inline (Droid-style); a full clear reads as a new window.
        with suppress(Exception):
            renderer.erase()
    if getattr(app, "is_running", False):
        with suppress(Exception):
            app.invalidate()


def _draw_menu(
    *,
    title: str,
    crumb: str,
    labels: list[str],
    index: int,
    erase_lines: int,
    multi_select: bool = False,
    checked: set[int] | None = None,
    header: str = "",
    letter_keys: bool = False,
    numbered: bool = True,
    note: str = "",
) -> None:
    out = sys.stdout
    w = _menu_paint_width()
    title, crumb, labels = _sanitize_menu(title, crumb, labels)
    checked = checked or set()
    if erase_lines:
        _erase_menu_block(erase_lines)
    for _ in range(_MENU_LEADING_LINES):
        write_menu_line()
    # With a header (e.g. "Ask User") the accent goes to the header and the
    # title reads as the plain question below it; otherwise the title is the
    # accent header (slash-command pickers).
    if header:
        for _ in range(_HEADER_SECTION_GAP):
            write_menu_line()
        write_menu_line(
            f"{ui_theme.PROMPT_ACCENT_ANSI}{_clip_to_row(f'{_BLOCK_INDENT}{header}', w)}{ui_theme.ANSI_RESET}"
        )
        write_menu_line(
            f"{ui_theme.TEXT_ANSI}{_clip_to_row(f'{_BLOCK_INDENT}{title}', w)}{ui_theme.ANSI_RESET}"
        )
    else:
        write_menu_line(
            f"{ui_theme.PROMPT_ACCENT_ANSI}{_clip_to_row(f'{_BLOCK_INDENT}{title}', w)}{ui_theme.ANSI_RESET}"
        )
    for line in _wrap_note(note, w):
        write_menu_line(
            f"{ui_theme.DIM_COUNTER_ANSI}{_clip_to_row(f'{_BLOCK_INDENT}{line}', w)}{ui_theme.ANSI_RESET}"
        )
    if crumb:
        write_menu_line(
            f"{ui_theme.DIM_COUNTER_ANSI}{_clip_to_row(f'{_BLOCK_INDENT}{crumb}', w)}{ui_theme.ANSI_RESET}"
        )
    # Airy: a blank line instead of a full-width rule between question and options.
    write_menu_line()
    for i, label in enumerate(labels):
        here = i == index
        token = _option_token(i, letter_keys=letter_keys, numbered=numbered)
        row_label = f"{token} {label}" if token else label
        if multi_select:
            box = _CHECKED if i in checked else _UNCHECKED
            _write_option_row(prefix=box, label=row_label, width=w, selected=here)
        else:
            sym = "❯" if here else " "
            _write_option_row(prefix=sym, label=row_label, width=w, selected=here)
    if multi_select:
        submit_selected = index == len(labels)
        _write_option_row(
            prefix="❯" if submit_selected else " ",
            label=_SUBMIT,
            width=w,
            selected=submit_selected,
        )
    hint = _select_hint(
        len(labels), letter_keys=letter_keys, numbered=numbered, multi_select=multi_select
    )
    write_menu_line()
    write_menu_line(
        f"{ui_theme.DIM_COUNTER_ANSI}{_clip_to_row(f'{_BLOCK_INDENT}{hint}', w)}{ui_theme.ANSI_RESET}"
    )
    out.flush()


def _erase_menu(
    crumb: str,
    labels: list[str],
    *,
    multi_select: bool = False,
    header: str = "",
    note: str = "",
    drawn_height: int | None = None,
) -> None:
    """Move cursor up to the start of this menu block and wipe it.

    ``drawn_height`` is the row count from the last paint. Recalculating from
    the current terminal width can disagree after a resize and delete the
    wrong number of rows.
    """
    _, crumb, labels = _sanitize_menu("", crumb, labels)
    height = (
        drawn_height
        if drawn_height is not None
        else _menu_height(crumb, labels, multi_select=multi_select, header=header, note=note)
    )
    _erase_menu_block(height, delete=True)
    sys.stdout.flush()


# ── picker loop ──────────────────────────────────────────────────────────────


def _pick(
    *,
    title: str,
    crumb: str,
    labels: list[str],
    initial_index: int = 0,
    custom_label: str | None = None,
    multi_select: bool = False,
    values: list[str] | None = None,
    header: str = "",
    letter_keys: bool = False,
    numbered: bool = True,
    note: str = "",
) -> int | str | None:
    """Draw an inline menu; return index, custom typed string, or None on Esc.

    When ``custom_label`` matches the focused row, printable keys type on that
    row in place (Droid-style) and Enter returns the typed string.

    When ``multi_select`` is True, return a newline-joined string of checked
    **values** (``values[i]`` when provided, else ``labels[i]``). Submit commits.
    Space/Enter and the visible row keys (``1-9`` or ``A-…``) toggle checkboxes.
    """
    from surfaces.shared.terminal.components.key_reader import read_menu_or_char

    if not labels:
        return None
    title, crumb, labels = _sanitize_menu(title, crumb, labels)
    selected_values = list(values) if values is not None else list(labels)
    if len(selected_values) != len(labels):
        selected_values = list(labels)
    idx = initial_index % len(labels)
    height = _menu_height(crumb, labels, multi_select=multi_select, header=header, note=note)
    draft = ""
    first = True
    checked: set[int] = set()
    custom_index = labels.index(custom_label) if custom_label in labels else -1
    row_count = len(labels) + (1 if multi_select else 0)
    while True:
        on_custom = custom_label is not None and idx < len(labels) and labels[idx] == custom_label
        display = list(labels)
        if on_custom:
            display[idx] = f"{draft}█"
        _draw_menu(
            title=title,
            crumb=crumb,
            labels=display,
            index=idx,
            erase_lines=0 if first else height,
            multi_select=multi_select,
            checked=checked if multi_select else None,
            header=header,
            letter_keys=letter_keys,
            numbered=numbered,
            note=note,
        )
        first = False
        height = _menu_height(crumb, display, multi_select=multi_select, header=header, note=note)
        if on_custom:
            action = read_menu_or_char(allow_chars=True)
        elif multi_select:
            action = read_menu_or_char(allow_chars=False, alpha_keys=letter_keys)
        elif letter_keys:
            action = _read_action(alpha_keys=True)
        else:
            action = _read_action()
        if on_custom and action == "backspace":
            draft = draft[:-1]
            if multi_select and custom_index >= 0 and not draft.strip():
                checked.discard(custom_index)
            continue
        if on_custom and len(action) == 1 and action.isprintable() and action not in "\t\n\r":
            draft += action
            if multi_select and custom_index >= 0 and draft.strip():
                checked.add(custom_index)
            continue
        if action == "up":
            idx = (idx - 1) % row_count
            continue
        if action == "down":
            idx = (idx + 1) % row_count
            continue
        if multi_select:
            toggle_index: int | None = None
            if action in (" ", "enter") and idx < len(labels):
                toggle_index = idx
            else:
                picked = _option_index_from_key(action, letter_keys=letter_keys, numbered=numbered)
                if picked is not None and 0 <= picked < len(labels):
                    toggle_index = picked
            if toggle_index is not None:
                if toggle_index == custom_index and not draft.strip():
                    idx = toggle_index
                    continue
                if toggle_index in checked:
                    checked.discard(toggle_index)
                else:
                    checked.add(toggle_index)
                idx = toggle_index
                continue
            if action == "enter" and idx == len(labels):
                parts: list[str] = []
                for index in sorted(checked):
                    if index == custom_index:
                        text = draft.strip()
                        if text:
                            parts.append(text)
                        continue
                    if 0 <= index < len(selected_values):
                        parts.append(selected_values[index])
                if not parts:
                    continue
                _erase_menu(
                    crumb, display, multi_select=True, header=header, note=note, drawn_height=height
                )
                return "\n".join(parts)
            if action in ("cancel", "eof"):
                _erase_menu(
                    crumb, display, multi_select=True, header=header, note=note, drawn_height=height
                )
                return None
            continue
        select_index = (
            None
            if on_custom
            else _option_index_from_key(action, letter_keys=letter_keys, numbered=numbered)
        )
        if select_index is not None:
            if 0 <= select_index < len(labels):
                if select_index == custom_index:
                    idx = select_index
                    continue
                _erase_menu(crumb, labels, header=header, note=note, drawn_height=height)
                return select_index
            continue
        if action == "enter":
            if on_custom:
                text = draft.strip()
                if not text:
                    continue
                _erase_menu(crumb, display, header=header, note=note, drawn_height=height)
                return text
            _erase_menu(crumb, labels, header=header, note=note, drawn_height=height)
            return idx
        if action in ("cancel", "eof"):
            _erase_menu(
                crumb,
                display if on_custom else labels,
                header=header,
                note=note,
                drawn_height=height,
            )
            return None
        if action == "ignore":
            continue


def repl_choose_one(
    *,
    title: str,
    choices: list[tuple[str, str]],
    breadcrumb: str = "",
    initial_value: str | None = None,
    custom_label: str | None = None,
    multi_select: bool = False,
    header: str = "",
    letter_keys: bool = False,
    numbered: bool = True,
    note: str = "",
) -> str | None:
    """Show an inline erasing arrow-key menu; return selected value or None on Esc.

    ``breadcrumb`` is a slash-separated path shown dimly below the title, e.g.
    ``/model › set``.  Only call when :func:`repl_tty_interactive` is True.

    ``header`` (e.g. ``Ask User``) renders as an accent line above the title,
    which then reads as the plain question; omit it for slash-command pickers.

    ``note`` is dim intro copy inside the menu block so leaving (Esc, a pick)
    erases it with the options instead of leaving it in the transcript.

    When ``custom_label`` is set and that row is focused, the user types on that
    row in place (same option array) instead of opening a separate prompt.

    When ``multi_select`` is True, checkboxes appear and the return value is a
    newline-joined string of selected **values** (``choices[i][0]``).
    """
    from surfaces.shared.terminal.components.cpr_stdin import drain_stale_cpr_bytes

    if not choices or not repl_tty_interactive():
        return None
    _clear_prompt_toolkit_paint()
    drain_stale_cpr_bytes()
    hide_terminal_cursor()
    try:
        crumb = breadcrumb
        labels = [label for _value, label in choices]
        values = [value for value, _label in choices]
        initial_index = 0
        if initial_value is not None:
            for index, (value, _label) in enumerate(choices):
                if value == initial_value:
                    initial_index = index
                    break
        picked = _pick(
            title=title,
            crumb=crumb,
            labels=labels,
            initial_index=initial_index,
            custom_label=custom_label,
            multi_select=multi_select,
            values=values,
            header=header,
            letter_keys=letter_keys,
            numbered=numbered,
            note=note,
        )
        if picked is None:
            return None
        if isinstance(picked, str):
            return picked
        value = choices[picked][0]
        return value if isinstance(value, str) else None
    finally:
        leave_inline_menu()


def print_valid_choice_list(
    console: Console,
    *,
    title: str,
    choices: list[str],
) -> None:
    """Print one choice per line for scan-friendly fallback/error messaging."""
    if not choices:
        return
    title = escape(strip_terminal_controls(title))
    console.print(f"[{ui_theme.SECONDARY}]{title}[/]")
    for index, choice in enumerate(choices, start=1):
        safe = escape(strip_terminal_controls(choice))
        console.print(f"[{ui_theme.SECONDARY}]  {index}. {safe}[/]")


__all__ = [
    "CRUMB_SEP",
    "erase_menu_lines",
    "hide_terminal_cursor",
    "leave_inline_menu",
    "menu_columns",
    "print_valid_choice_list",
    "read_menu_action",
    "repl_choose_one",
    "ensure_tty_column_zero",
    "prepare_repl_output_line",
    "repl_section_break",
    "repl_tty_interactive",
    "reset_tty_column",
    "show_terminal_cursor",
    "write_menu_line",
]

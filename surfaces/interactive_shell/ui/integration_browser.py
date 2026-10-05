"""List-first browser for configured integrations and MCP servers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from shutil import get_terminal_size
from typing import Literal

from prompt_toolkit.output import create_output

from infrastructure.terminal import theme as ui_theme
from surfaces.shared.terminal.components.choice_menu import (
    enter_inline_menu,
    leave_inline_menu,
    repl_tty_interactive,
)
from surfaces.shared.terminal.components.key_reader import read_menu_or_char
from surfaces.shared.terminal.prompt_layout import clip_prompt_text, prompt_text_width


@dataclass(frozen=True)
class IntegrationEntry:
    """Safe display metadata, without configuration values or credentials."""

    service: str
    can_verify: bool


@dataclass(frozen=True)
class IntegrationSelection:
    """An explicit action; a missing service requests the setup picker."""

    action: Literal["verify", "setup", "remove"]
    service: str | None = None


@dataclass(frozen=True)
class _Frame:
    rows: tuple[str, ...]
    top: int


def _styled(text: str, style: str, width: int) -> str:
    return f"{style}{clip_prompt_text(text, width)}{ui_theme.ANSI_RESET}"


def _render_frame(
    entries: Sequence[IntegrationEntry],
    *,
    selected: int,
    top: int,
    mcp: bool,
    width: int,
    height: int,
) -> _Frame:
    if width < 39 or height < 12:
        notice = ("Esc close", "Resize to at least 40×12", "to browse connections.")
        return _Frame(
            tuple(
                _styled(row, ui_theme.DIM_COUNTER_ANSI, width)
                for row in notice[: max(0, height - 1)]
            ),
            top,
        )

    title = "MCP servers" if mcp else "Integrations"
    add, remove = ("connect", "disconnect") if mcp else ("add", "remove")
    rows = [_styled(f"  {title}", ui_theme.PROMPT_ACCENT_ANSI, width)]
    name_width = min(34, width - 19)
    if height >= 20:
        rows.append("")
        rows.append(
            _styled(
                f"    {'SERVER' if mcp else 'INTEGRATION':<{name_width}} STATUS",
                ui_theme.DIM_COUNTER_ANSI,
                width,
            )
        )
    rule = _styled("  " + "─" * (width - 2), ui_theme.DIM_COUNTER_ANSI, width)
    rows.append(rule)
    # Leave room for the selected-item preview and three complete control rows.
    visible = min(len(entries), 20, max(1, height - len(rows) - 9))
    top = max(0, min(top, len(entries) - visible, selected))
    top = max(top, selected - visible + 1)
    for index in range(top, top + visible):
        name = clip_prompt_text(entries[index].service, name_width)
        name += " " * (name_width - prompt_text_width(name))
        prefix = f"  {'›' if index == selected else ' '} {name} "
        status = "Not checked" if entries[index].can_verify else "Configured"
        if index == selected:
            row = prefix + status
            rows.append(
                _styled(
                    row + " " * max(0, width - prompt_text_width(row)),
                    ui_theme.MENU_SELECTION_ROW_ANSI,
                    width,
                )
            )
        else:
            rows.append(
                _styled(prefix, ui_theme.TEXT_ANSI, width)
                + _styled(status, ui_theme.SECONDARY_ANSI, 11)
            )
    if not entries:
        rows.append(
            _styled(
                f"  No {'MCP servers' if mcp else 'integrations'} configured.",
                ui_theme.TEXT_ANSI,
                width,
            )
        )
    rows.extend(["", rule])
    if entries:
        rows.append(_styled(f"  {entries[selected].service}", ui_theme.HIGHLIGHT_ANSI, width))
        detail = (
            "Configured; connectivity not checked."
            if entries[selected].can_verify
            else "No connectivity check available."
        )
        rows.append(_styled(f"  {detail}", ui_theme.SECONDARY_ANSI, width))
    else:
        rows.append(
            _styled(f"  Press a to {add} your first connection.", ui_theme.SECONDARY_ANSI, width)
        )
        rows.append("")
    rows.append("")
    position = f"{selected + 1}/{len(entries)}" if entries else "0 configured"
    controls = "↑↓ browse" if entries else f"a {add}"
    if entries and entries[selected].can_verify:
        controls += " · Enter verify"
    rows.append(_styled(f"  {controls}", ui_theme.DIM_COUNTER_ANSI, width))
    actions = f"a {add} · s setup · r {remove}" if entries else ""
    rows.append(_styled(f"  {actions}", ui_theme.DIM_COUNTER_ANSI, width))
    rows.append(_styled(f"  Esc close  ·  {position}", ui_theme.DIM_COUNTER_ANSI, width))
    return _Frame(tuple(rows), top)


def browse_integrations(
    entries: Sequence[IntegrationEntry], *, mcp: bool = False
) -> IntegrationSelection | None:
    """Browse without probes; return an explicit action after restoring the terminal."""
    if not repl_tty_interactive():
        return None
    selected = top = 0
    output = create_output()
    enter_inline_menu()
    try:
        output.enter_alternate_screen()
        while True:
            size = get_terminal_size(fallback=(80, 24))
            frame = _render_frame(
                entries,
                selected=selected,
                top=top,
                mcp=mcp,
                width=max(1, size.columns - 1),
                height=size.lines,
            )
            output.cursor_goto(0, 0)
            output.erase_down()
            for row in frame.rows:
                output.write_raw(f"{row}\r\n")
            output.flush()
            top = frame.top
            key = read_menu_or_char(allow_chars=True)
            if key in ("cancel", "eof"):
                return None
            if size.columns < 40 or size.lines < 12:
                continue
            if key == "a":
                return IntegrationSelection("setup")
            if not entries:
                continue
            if key in ("up", "down", "tab"):
                selected = (selected + (-1 if key == "up" else 1)) % len(entries)
            elif key in ("enter", "s", "r"):
                if key == "enter" and not entries[selected].can_verify:
                    continue
                action: Literal["verify", "setup", "remove"] = "verify"
                if key == "s":
                    action = "setup"
                elif key == "r":
                    action = "remove"
                return IntegrationSelection(action, entries[selected].service)
    finally:
        try:
            output.quit_alternate_screen()
            output.flush()
        finally:
            leave_inline_menu()

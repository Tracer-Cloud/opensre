"""Bounded, theme-aware presentation for opt-in single-choice menus."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import infrastructure.terminal.theme as ui_theme
from infrastructure.safety.terminal_output import strip_terminal_controls
from surfaces.shared.terminal.prompt_layout import clip_prompt_text, prompt_text_width

_MAX_VISIBLE_CHOICES = 6


@dataclass(frozen=True)
class MenuPanel:
    """A painted viewport and the choice indexes its numbered shortcuts address."""

    lines: list[str]
    choice_indices: range


def _search_field(query: str, width: int) -> str:
    """Keep the most recently typed text and caret visible in narrow viewports."""
    query = strip_terminal_controls(query)
    if prompt_text_width(f"/{query}▏") <= width:
        return f"/{query}▏"
    tail: list[str] = []
    remaining = max(0, width - 3)
    for char in reversed(query):
        size = prompt_text_width(char)
        if size > remaining:
            break
        tail.append(char)
        remaining -= size
    return clip_prompt_text("/…" + "".join(reversed(tail)) + "▏", width)


def build_menu_panel(
    *,
    title: str,
    breadcrumb: str,
    labels: Sequence[str],
    index: int,
    width: int,
    max_height: int,
    note: str = "",
    current_index: int | None = None,
    numbered: bool = True,
    searchable: bool = False,
    search_query: str | None = None,
) -> MenuPanel:
    """Return physical rows, keeping the focused choice visible within the viewport."""
    if (not labels and search_query is None) or width < 1 or max_height < 1:
        return MenuPanel([], range(0))
    has_choices = bool(labels)
    labels = labels or ["No matches"]
    index = max(0, min(index, len(labels) - 1))
    if width < 12 or max_height < 5:
        number = "1 " if numbered else ""
        text = f"› {number}{labels[index]}" if has_choices else "No matches"
        if search_query is not None:
            text = f"{_search_field(search_query, max(3, width // 2))} {text}"
        text = clip_prompt_text(text, width)
        # Cleanup records the full paint width, including this reduced-height layout.
        text += " " * (width - prompt_text_width(text))
        return MenuPanel(
            [f"{ui_theme.MENU_SELECTION_ROW_ANSI}{text}{ui_theme.ANSI_RESET}"],
            range(index, index + 1) if has_choices else range(0),
        )

    inner = width - 2
    metadata = [text for text in (breadcrumb if "›" in breadcrumb else "", note) if text]
    if search_query is not None:
        metadata.insert(0, "Search: " + _search_field(search_query, inner - 10))
    metadata = metadata[: max_height - 5]
    count = min(_MAX_VISIBLE_CHOICES, len(labels), max_height - 4 - len(metadata))
    start = min(max(0, index - count + 1), len(labels) - count)
    frame = ui_theme.PROMPT_FRAME_ANSI
    reset = ui_theme.ANSI_RESET

    def row(text: str, *, style: str = "", suffix: str = "") -> str:
        budget = inner - 2 - prompt_text_width(suffix)
        content = " " + clip_prompt_text(text, budget)
        content += " " * max(0, inner - prompt_text_width(content + suffix) - 1) + suffix + " "
        return f"{frame}│{reset}{style}{content}{reset}{frame}│{reset}"

    counter = f"{index + 1}/{len(labels)}" if has_choices else "0/0"
    if count < len(labels) and (search_query is None or metadata):
        counter = (
            ("↑ " if start else "")
            + f"{start + 1}–{start + count}/{len(labels)}"
            + (" ↓" if start + count < len(labels) else "")
        )
    heading_width = max(0, inner - len(counter) - 5)
    heading = (
        _search_field(search_query, heading_width)
        if search_query is not None and not metadata
        else clip_prompt_text(title, heading_width)
    )
    top = f"─ {heading} "
    top += "─" * max(0, inner - prompt_text_width(top) - len(counter) - 2)
    top += f" {counter} "
    top = clip_prompt_text(top, inner)
    lines = [f"{frame}╭{top}╮{reset}"]
    lines.extend(row(text, style=ui_theme.DIM_ANSI) for text in metadata)
    for item in range(start, start + count):
        marker = "›" if item == index and has_choices else " "
        number = f"{item - start + 1} " if numbered else ""
        suffix = "✓" if item == current_index else ""
        style = (
            ui_theme.MENU_SELECTION_ROW_ANSI
            if item == index and has_choices
            else ui_theme.TEXT_ANSI
        )
        lines.append(row(f"{marker} {number}{labels[item]}", style=style, suffix=suffix))
    lines.append(f"{frame}├{'─' * inner}┤{reset}")
    cancel = "back" if "›" in breadcrumb else "close"
    motion = "scroll" if count < len(labels) else "move"
    hint = f"↑↓ {motion}  Enter select  Esc {cancel}"
    if inner < 34:
        hint = f"↑↓ Enter Esc {cancel}"
    if searchable:
        hint = (
            f"↑↓ Enter Esc {cancel} / search"
            if inner < 48
            else f"↑↓ {motion}  Enter select  Esc {cancel}  / search"
        )
    if searchable and inner < 34:
        hint = "/ search  ↑↓ Enter Esc"
    if search_query is not None:
        hint = (
            "↑↓ Enter Esc clear" if inner < 48 else f"↑↓ {motion}  Enter select  Esc clear search"
        )
    if numbered and inner >= 58 and not searchable:
        keys = "1" if count == 1 else f"1–{count}"
        hint += f"  {keys} select"
    if current_index is not None and prompt_text_width(hint) + 12 <= inner:
        hint += "  ✓ current"
    lines.append(row(hint, style=ui_theme.DIM_ANSI))
    lines.append(f"{frame}╰{'─' * inner}╯{reset}")
    return MenuPanel(lines, range(start, start + count) if has_choices else range(0))

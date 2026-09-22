"""Bounded, theme-aware presentation for opt-in single-choice menus."""

from __future__ import annotations

from collections.abc import Sequence

import infrastructure.terminal.theme as ui_theme
from surfaces.shared.terminal.prompt_layout import clip_prompt_text, prompt_text_width

_MAX_VISIBLE_CHOICES = 6


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
) -> list[str]:
    """Return physical rows, keeping the focused choice visible within the viewport."""
    if not labels or width < 1 or max_height < 1:
        return []
    index = max(0, min(index, len(labels) - 1))
    if width < 12 or max_height < 5:
        text = clip_prompt_text(f"› {labels[index]}", width)
        # Cleanup records the full paint width, including this reduced-height layout.
        text += " " * (width - prompt_text_width(text))
        return [f"{ui_theme.MENU_SELECTION_ROW_ANSI}{text}{ui_theme.ANSI_RESET}"]

    inner = width - 2
    metadata = [text for text in (breadcrumb if "›" in breadcrumb else "", note) if text]
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

    counter = f"{index + 1}/{len(labels)}"
    heading = clip_prompt_text(title, max(0, inner - len(counter) - 5))
    top = f"─ {heading} "
    top += "─" * max(0, inner - prompt_text_width(top) - len(counter) - 2)
    top += f" {counter} "
    top = clip_prompt_text(top, inner)
    lines = [f"{frame}╭{top}╮{reset}"]
    lines.extend(row(text, style=ui_theme.DIM_ANSI) for text in metadata)
    for item in range(start, start + count):
        marker = "›" if item == index else " "
        number = f"{item + 1} " if numbered else ""
        suffix = "✓" if item == current_index else ""
        style = ui_theme.MENU_SELECTION_ROW_ANSI if item == index else ui_theme.TEXT_ANSI
        lines.append(row(f"{marker} {number}{labels[item]}", style=style, suffix=suffix))
    lines.append(f"{frame}├{'─' * inner}┤{reset}")
    cancel = "back" if "›" in breadcrumb else "close"
    hint = f"↑↓ move  Enter select  Esc {cancel}"
    if inner < 34:
        hint = f"↑↓ Enter Esc {cancel}"
    if numbered and inner >= 58:
        hint += f"  1–{min(9, len(labels))} select"
    if current_index is not None and prompt_text_width(hint) + 12 <= inner:
        hint += "  ✓ current"
    lines.append(row(hint, style=ui_theme.DIM_ANSI))
    lines.append(f"{frame}╰{'─' * inner}╯{reset}")
    return lines

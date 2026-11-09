from __future__ import annotations

from rich.text import Text

from infrastructure.terminal.theme import (
    BRAND,
    DIM,
    ERROR,
    GLYPH_BULLET,
    GLYPH_ERROR,
    GLYPH_SUCCESS,
    HIGHLIGHT,
    SECONDARY,
    TEXT,
)
from surfaces.shared.terminal.components.time_format import _elapsed_hms
from surfaces.shared.terminal.output.console_state import _get_console
from surfaces.shared.terminal.output.environment import (
    _is_silent_output,
    _safe_print,
    get_output_format,
)
from surfaces.shared.terminal.output.labels import BADGE_STYLES


def render_divider(width: int = 80) -> None:
    """Print a DIM-coloured dashed divider."""
    if _is_silent_output():
        return
    if get_output_format() == "rich":
        _get_console().print(Text("┄" * width, style=DIM))
    else:
        _safe_print("─" * width)


def render_footer(
    phase: str,
    elapsed: float,
    model: str,
    mode: str,
    *,
    show_cancel: bool = True,
) -> None:
    """Print the persistent status footer line."""
    if _is_silent_output():
        return
    if get_output_format() == "rich":
        t = Text()
        t.append(" ● ", style=f"bold {HIGHLIGHT}")
        t.append(f"{phase}  ", style=f"bold {SECONDARY}")
        t.append(f"{_elapsed_hms(elapsed)}  ", style=SECONDARY)
        if model:
            t.append(f"{model}  ", style=SECONDARY)
        t.append(f"{mode}  ", style=SECONDARY)
        if show_cancel:
            t.append("esc to cancel", style=DIM)
        _get_console().print(t)
    else:
        _safe_print(f"● {phase}  {elapsed:.1f}s  {model}  {mode}")


def render_event(
    event_type: str,
    message: str,
    *,
    insight: str | None = None,
    muted: bool = False,
    elapsed_s: float = 0.0,
    glyph: str = GLYPH_SUCCESS,
    error: bool = False,
) -> None:
    """Print one typed event-log row."""
    if _is_silent_output():
        return
    if get_output_format() == "rich":
        badge_label, badge_color = BADGE_STYLES.get(event_type, BADGE_STYLES["DIAG"])
        t = Text()
        t.append(f"{_elapsed_hms(elapsed_s)}  ", style=SECONDARY)
        if muted:
            t.append(f"{glyph}  ", style=SECONDARY)
            msg_style = SECONDARY
        elif error:
            t.append(f"{GLYPH_ERROR}  ", style=f"bold {ERROR}")
            msg_style = TEXT
        else:
            t.append(f"{glyph}  ", style=f"bold {HIGHLIGHT}")
            msg_style = TEXT
        t.append(badge_label, style=f"bold {badge_color}")
        t.append("  ·  ", style=DIM)
        t.append(message, style=msg_style)
        if insight:
            t.append(f"  ↳ {insight}", style=BRAND)
        _get_console().print(t)
    else:
        mark = GLYPH_ERROR if error else (GLYPH_BULLET if muted else GLYPH_SUCCESS)
        line = f"  {mark}  [{event_type}]  {message}"
        if insight:
            line += f"  ↳ {insight}"
        _safe_print(line)

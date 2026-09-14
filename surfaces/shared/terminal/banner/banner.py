"""Launch banner shared by the CLI landing page and REPL.

Droid-style centered hero: each row is centered independently (a single
``Align.center`` on a multi-line block left-aligns short lines inside the
widest line). Bold block wordmark + version + welcome + capability chips.
"""

from __future__ import annotations

import enum
import math
import sys
import threading
import time
from dataclasses import dataclass

from rich.align import Align
from rich.cells import cell_len
from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.text import Text

from config.constants import (
    CI_FIX_COUNT_LABEL,
    PRODUCT_DISPLAY_NAME,
    WELCOME_DESCRIPTION,
    WELCOME_TITLE,
)
from config.version import get_opensre_version
from infrastructure.terminal import theme as ui_theme
from infrastructure.terminal.theme import (
    BOLD_SKILL,
    BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    SECONDARY,
    TEXT,
)
from surfaces.shared.terminal.banner.banner_state import LaunchStatus, load_launch_status
from surfaces.shared.terminal.components.rendering import _console_is_capturing
from surfaces.shared.terminal.prompt_layout import clip_prompt_text

_BANNER_VERTICAL_PADDING = 1

#: Version prefix under the wordmark.
_VERSION_PREFIX = "v"
#: Capability-status glyphs: present/usable vs. absent.
_STATUS_OK_GLYPH = "✓"
_STATUS_MISSING_GLYPH = "✗"
#: Spacing between status items.
_STATUS_ITEM_GAP = "     "

#: Minimum console width to paint the ring mark (its cell width + margin);
#: narrower terminals get the compact text title instead.
_WORDMARK_MIN_WIDTH = 24


class LaunchStatusLabel(enum.StrEnum):
    """Labels for the launch banner's capability status line."""

    SKILLS = "Skills"
    CI_CD_FIXES = CI_FIX_COUNT_LABEL


#: The canonical overlapping-ring OpenSRE mark (docs/images/opensre-mark.svg),
#: rendered in braille — the "loops" logo.
_WORDMARK_ROWS: tuple[str, ...] = (
    "⠀⠀⠀⢀⣤⣶⣾⣿⣿⣿⣿⣶⣦⣄⡈⠒⢦⣄⡀⠀⠀⠀",
    "⠀⢀⣴⣿⠿⠋⢁⣤⣶⠖⠂⠉⠙⢿⣿⣦⠀⠙⣿⣦⡀⠀",
    "⢀⣾⣿⠋⠀⣴⣿⡟⠁⠀⠀⠀⠀⠀⠹⣿⣷⠀⠘⣿⣷⡀",
    "⣼⣿⡇⠀⣼⣿⡿⠀⠀⠀⠀⠀⠀⠀⠀⢹⣿⡇⠀⢹⣿⣇",
    "⣿⣿⠀⠀⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⠀⢸⣿⣿",
    "⣿⣿⠀⠀⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⠀⢸⣿⣿",
    "⢻⣿⣇⠀⢻⣿⣷⠀⠀⠀⠀⠀⠀⠀⠀⣼⣿⡇⠀⣸⣿⡏",
    "⠈⢿⣿⣄⠀⠻⣿⣧⡀⠀⠀⠀⠀⠀⣰⣿⡟⠀⢠⣿⡿⠁",
    "⠀⠈⠻⣿⣷⣄⣈⠛⠻⠶⠄⣀⣤⣾⣿⠟⠀⣰⣿⠟⠀⠀",
    "⠀⠀⠀⠈⠙⠻⠿⣿⣿⣿⡿⠿⠟⠋⠀⠴⠞⠋⠁⠀⠀⠀",
)

# A short 60 FPS startup turn; animation stops before the prompt becomes live.
_WORDMARK_SPIN_FRAME_COUNT = 48
_WORDMARK_SPIN_FRAME_INTERVAL_SECONDS = 1 / 60
# When the spin runs under startup work and is asked to stop, it still shows
# at least this many frames so it always reads as a turn, never a flicker.
_WORDMARK_SPIN_MIN_FRAMES = 24
_MIN_PROJECTED_SCALE = 0.08
_BRAILLE_BASE = 0x2800
_BRAILLE_LIMIT = 0x28FF
_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"
_ERASE_LINE = "\x1b[2K"
_COLUMN_ONE = "\x1b[1G"
_SYNCED_OUTPUT_START = "\x1b[?2026h"
_SYNCED_OUTPUT_END = "\x1b[?2026l"


@dataclass(frozen=True, slots=True)
class WordmarkSpinFrame:
    """One projected frame of the terminal wordmark's Y-axis turn."""

    rows: tuple[str, ...]
    scale: float
    back_facing: bool


def _center(renderable: RenderableType) -> Align:
    """Center one row/block on its own — do not bundle unequal-width lines."""
    return Align.center(renderable)


def _braille_dot_columns(row: str) -> list[int]:
    """Decode braille cells into four-bit vertical dot columns."""
    columns: list[int] = []
    for cell in row:
        codepoint = ord(cell)
        dots = codepoint - _BRAILLE_BASE if _BRAILLE_BASE <= codepoint <= _BRAILLE_LIMIT else 0
        left = dots & 0x07 | ((dots & 0x40) >> 3)
        right = ((dots & 0x38) >> 3) | ((dots & 0x80) >> 4)
        columns.extend((left, right))
    return columns


def _encode_braille_columns(columns: list[int]) -> str:
    """Encode four-bit vertical dot columns back into braille cells."""
    cells: list[str] = []
    for index in range(0, len(columns), 2):
        left = columns[index]
        right = columns[index + 1] if index + 1 < len(columns) else 0
        dots = (left & 0x07) | ((left & 0x08) << 3) | ((right & 0x07) << 3) | ((right & 0x08) << 4)
        cells.append(chr(_BRAILLE_BASE + dots))
    return "".join(cells)


def _turn_wordmark_row(row: str, *, scale: float, mirrored: bool) -> str:
    """Project one logo row onto a narrower plane for a 3D turn frame."""
    source = _braille_dot_columns(row)
    if mirrored:
        source.reverse()
    # A braille cell is two dot columns wide. An odd projected width cannot be
    # centered on the even-width source canvas, so its extra dot alternates
    # sides as the logo turns and makes the mark appear to wobble.
    target_width = max(2, 2 * round(len(source) * scale / 2))
    if target_width >= len(source):
        projected = source
    else:
        last_source_index = len(source) - 1
        last_target_index = target_width - 1
        projected = [
            source[round(index * last_source_index / last_target_index)]
            for index in range(target_width)
        ]
    # Keep the encoded row width fixed. Re-centering a shorter string on every
    # frame moves it by whole terminal cells, producing a visible side-to-side
    # jump even though the dot projection itself changes smoothly.
    canvas = [0] * len(source)
    start = (len(canvas) - len(projected)) // 2
    canvas[start : start + len(projected)] = projected
    return _encode_braille_columns(canvas)


def build_wordmark_spin_frames() -> tuple[WordmarkSpinFrame, ...]:
    """Build one full Y-axis revolution of the terminal wordmark."""
    frames: list[WordmarkSpinFrame] = []
    for frame_index in range(_WORDMARK_SPIN_FRAME_COUNT):
        angle = math.tau * frame_index / _WORDMARK_SPIN_FRAME_COUNT
        cosine = math.cos(angle)
        scale = max(abs(cosine), _MIN_PROJECTED_SCALE)
        mirrored = cosine < 0
        frames.append(
            WordmarkSpinFrame(
                rows=tuple(
                    _turn_wordmark_row(row, scale=scale, mirrored=mirrored)
                    for row in _WORDMARK_ROWS
                ),
                scale=scale,
                back_facing=mirrored,
            )
        )
    return tuple(frames)


def _frame_style(frame: WordmarkSpinFrame) -> str:
    if frame.scale <= 0.18:
        return ui_theme.DIM_ANSI
    if frame.back_facing:
        return ui_theme.SECONDARY_ANSI
    return ui_theme.HIGHLIGHT_ANSI


def _animation_frame(
    frame: WordmarkSpinFrame,
    *,
    width: int,
    rewind: bool,
) -> str:
    """Paint one frame in the temporary startup region."""
    style = _frame_style(frame)
    rows = [""]
    for row in frame.rows:
        left_pad = max((width - cell_len(row)) // 2, 0)
        rows.append(f"{' ' * left_pad}{style}\x1b[1m{row}{ui_theme.ANSI_RESET}")
    frame_text = f"{_COLUMN_ONE}{_ERASE_LINE}" + (f"\r\n{_COLUMN_ONE}{_ERASE_LINE}".join(rows))
    move_to_top = f"{_COLUMN_ONE}\x1b[{len(rows) - 1}A" if rewind else ""
    return f"{_SYNCED_OUTPUT_START}{move_to_top}{frame_text}{_SYNCED_OUTPUT_END}"


def _clear_animation(frame: WordmarkSpinFrame) -> str:
    row_count = len(frame.rows) + 1
    move_to_top = f"{_COLUMN_ONE}\x1b[{row_count - 1}A"
    clear_rows = _ERASE_LINE + (f"\x1b[1B{_COLUMN_ONE}{_ERASE_LINE}" * (row_count - 1))
    return (
        f"{_SYNCED_OUTPUT_START}{move_to_top}{clear_rows}"
        f"\x1b[{row_count - 1}A{_COLUMN_ONE}{_SYNCED_OUTPUT_END}"
    )


def animate_launch_wordmark(
    console: Console,
    *,
    stop: threading.Event | None = None,
    min_frames: int = _WORDMARK_SPIN_MIN_FRAMES,
) -> None:
    """Turn the terminal wordmark once before the interactive prompt starts.

    With ``stop``, the turn ends early once the event is set and at least
    ``min_frames`` have shown — so the runtime can boot under the animation and
    the prompt appears as soon as it is ready instead of after a fixed delay.
    """
    if (
        console.file is not sys.stdout
        or not sys.stdout.isatty()
        or _console_is_capturing(console)
        or console.width <= _wordmark_cell_width()
    ):
        return

    frames = build_wordmark_spin_frames()
    stream = sys.stdout
    try:
        stream.write(_HIDE_CURSOR)
        for index, frame in enumerate(frames):
            if stop is not None and stop.is_set() and index >= min_frames:
                break
            stream.write(
                _animation_frame(
                    frame,
                    width=console.width,
                    rewind=index > 0,
                )
            )
            stream.flush()
            time.sleep(_WORDMARK_SPIN_FRAME_INTERVAL_SECONDS)
    finally:
        stream.write(_clear_animation(frames[0]))
        stream.write(_SHOW_CURSOR)
        stream.flush()


def _build_wordmark(*, console_width: int) -> Text:
    """Return the bold ring "loops" mark, or a compact title on narrow terminals."""
    if console_width < _WORDMARK_MIN_WIDTH:
        return Text(PRODUCT_DISPLAY_NAME, style=f"bold {HIGHLIGHT}", no_wrap=True)
    return Text("\n".join(_WORDMARK_ROWS), style=f"bold {HIGHLIGHT}", no_wrap=True)


def _append_status_item(
    line: Text,
    label: str,
    count: int | None,
    *,
    available: bool,
    mark_unavailable: bool = True,
) -> None:
    if line:
        line.append(_STATUS_ITEM_GAP, style=DIM)
    muted = not available and not mark_unavailable
    line.append(label, style=DIM if muted else f"bold {TEXT}")
    if count is not None:
        line.append(f" ({count})", style=DIM if muted else SECONDARY)
    if muted:
        return
    glyph = _STATUS_OK_GLYPH if available else _STATUS_MISSING_GLYPH
    # Green success / red missing — same signal language as Droid's chips.
    line.append(f" {glyph}", style=BOLD_SKILL if available else ERROR)


def _build_version_line() -> Text:
    return Text(
        f"{_VERSION_PREFIX}{get_opensre_version()}",
        style=f"bold {BRAND}",
        no_wrap=True,
    )


def _build_welcome_title() -> Text:
    """Accent title — same copy and style as the sign-in screen."""
    return Text(WELCOME_TITLE, style=f"bold {HIGHLIGHT}", no_wrap=True)


def _build_welcome_paragraph() -> Text:
    """One-sentence product description, wrapped and centered (not clipped)."""
    return Text(WELCOME_DESCRIPTION, style=str(TEXT), justify="center")


def _build_capabilities(status: LaunchStatus, *, max_width: int) -> Text:
    capabilities = Text(overflow="fold", no_wrap=True)
    _append_status_item(
        capabilities,
        LaunchStatusLabel.SKILLS,
        status.skill_count,
        available=status.skill_count > 0,
    )
    _append_status_item(
        capabilities,
        LaunchStatusLabel.CI_CD_FIXES,
        status.ci_fix_count,
        available=status.ci_fix_count > 0,
        mark_unavailable=False,
    )
    # Clip rather than soft-wrap — a wrapped chip row looks left-ragged.
    plain = capabilities.plain
    if cell_len(plain) > max_width:
        return Text(
            clip_prompt_text(plain, max_width),
            style=str(TEXT),
            no_wrap=True,
        )
    return capabilities


def build_launch_banner(
    console: Console | None = None,
    *,
    session: object = None,
) -> RenderableType:
    """Build the centered, borderless OpenSRE launch banner."""
    del session  # Reserved for future session-scoped launch indicators.
    console = console or Console(
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    status = load_launch_status()
    width = console.width
    # Leave one column empty so the banner never soft-wraps on the last cell.
    line_width = max(width - 1, 1)
    # Rows top-to-bottom (``None`` is a blank spacer). Each is centered on its
    # own axis in the loop below — one Align.center over a multi-line block
    # would left-align the short lines inside the widest one.
    rows: list[RenderableType | None] = [
        _build_wordmark(console_width=width),
        None,
        _build_version_line(),
        None,
        _build_welcome_title(),
        _build_welcome_paragraph(),
        None,
        _build_capabilities(status, max_width=line_width),
    ]
    body: RenderableType = Group(*(Text() if row is None else _center(row) for row in rows))
    return Padding(body, (_BANNER_VERTICAL_PADDING, 0))


def render_launch_banner(
    console: Console | None = None,
    *,
    session: object = None,
    animate: bool = True,
) -> None:
    """Print the OpenSRE launch banner.

    ``animate=False`` skips the startup spin — used on terminal resize where the
    banner must be reprinted instantly at the new width.
    """
    console = console or Console(
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    banner = build_launch_banner(console, session=session)
    if animate:
        animate_launch_wordmark(console)
    console.print(banner)


def _wordmark_cell_width() -> int:
    """Widest wordmark row in terminal cells (for tests / narrow fallback)."""
    return max(cell_len(row) for row in _WORDMARK_ROWS)


__all__ = [
    "WordmarkSpinFrame",
    "animate_launch_wordmark",
    "build_launch_banner",
    "build_wordmark_spin_frames",
    "render_launch_banner",
]

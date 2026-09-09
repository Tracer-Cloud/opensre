"""Shared color theme for the OpenSRE CLI.

Single source of truth for every colour rendered to the terminal. Eight
semantic tokens — never introduce new hexes, never use Rich named colours
(red / yellow / cyan / ...), never embed raw ANSI colour escapes outside
this module.

Token reference
---------------
  HIGHLIGHT  brand name, ❯ prompt, ✓ success, /commands, key findings, live indicator
             (Thinking… / stage spinner lead)
  BRAND      model name, file paths, version numbers, secondary labels
             (Invoking tools… spinner lead — distinct from Thinking)
  TEXT       all primary body text, step names, values, section headers
  SECONDARY  tips, descriptions, muted info, secondary body text
  DIM        timestamps, dividers, labels, ruled-out items, dim context
  WARNING    warnings only — no auth, fallback store, config issues
  ERROR      errors only — missing required config, failures
  BG         terminal background, never used as foreground
  INPUT_SURFACE  composer/menu plate — visibly lifted vs BG (input box fill)
  BOLD_SKILL fixed green skill-activation label
  reply marker  assistant ``Ω`` lead-in — Factory/Droid-warm accent via
                :func:`reply_marker_style` (not WARNING; must stay vivid)

Usage
-----
  from infrastructure.terminal.theme import HIGHLIGHT, ERROR, DIM
  console.print(f"[{HIGHLIGHT}]✓ success[/]")
  console.print(f"[{ERROR}]✗ failed[/]")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import SupportsIndex

from rich.theme import Theme

from config.constants.repl_theme import DEFAULT_THEME_NAME, THEME_NAMES


@dataclass(frozen=True)
class CliTheme:
    """Named palette for interactive shell rendering."""

    name: str
    HIGHLIGHT: str
    BRAND: str
    TEXT: str
    SECONDARY: str
    DIM: str
    WARNING: str
    ERROR: str
    BG: str
    INPUT_SURFACE: str


THEME_REGISTRY: dict[str, CliTheme] = {
    "green": CliTheme(
        name="green",
        HIGHLIGHT="#C0E2BA",
        BRAND="#70977F",
        TEXT="#B6BAC2",
        SECONDARY="#A6A6A6",
        DIM="#6E6E6E",
        WARNING="#CEA25C",
        ERROR="#C45B52",
        BG="#15161A",
        # Lifted plate for the composer (Droid/Claude/Cursor-style). Must read
        # clearly against BG — the old #1C1D22 was nearly invisible.
        INPUT_SURFACE="#252830",
    ),
    "blue": CliTheme(
        name="blue",
        HIGHLIGHT="#E0CC9C",
        BRAND="#B2935B",
        TEXT="#D0D0D0",
        SECONDARY="#B0A898",
        DIM="#6E6E6E",
        WARNING="#E0B466",
        ERROR="#CF6B63",
        BG="#15161A",
        # Lifted plate for the composer (Droid/Claude/Cursor-style). Must read
        # clearly against BG — the old #1C1D22 was nearly invisible.
        INPUT_SURFACE="#252830",
    ),
    "amber": CliTheme(
        name="amber",
        HIGHLIGHT="#E0CC9C",
        BRAND="#B2935B",
        TEXT="#D0D0D0",
        SECONDARY="#B0A898",
        DIM="#6E6E6E",
        WARNING="#E0B466",
        ERROR="#CF6B63",
        BG="#15161A",
        # Lifted plate for the composer (Droid/Claude/Cursor-style). Must read
        # clearly against BG — the old #1C1D22 was nearly invisible.
        INPUT_SURFACE="#252830",
    ),
    "mono": CliTheme(
        name="mono",
        HIGHLIGHT="#C6C6C6",
        BRAND="#A7A7A7",
        TEXT="#B6BAC2",
        SECONDARY="#A6A6A6",
        DIM="#6E6E6E",
        WARNING="#B0B0B0",
        ERROR="#8E8E8E",
        BG="#15161A",
        # Lifted plate for the composer (Droid/Claude/Cursor-style). Must read
        # clearly against BG — the old #1C1D22 was nearly invisible.
        INPUT_SURFACE="#252830",
    ),
    "red": CliTheme(
        name="red",
        HIGHLIGHT="#EBAB9E",
        BRAND="#B06C66",
        TEXT="#B6BAC2",
        SECONDARY="#A6A6A6",
        DIM="#6E6E6E",
        WARNING="#E0B466",
        ERROR="#CF6B63",
        BG="#15161A",
        # Lifted plate for the composer (Droid/Claude/Cursor-style). Must read
        # clearly against BG — the old #1C1D22 was nearly invisible.
        INPUT_SURFACE="#252830",
    ),
    "pink": CliTheme(
        name="pink",
        HIGHLIGHT="#F2C0D9",
        BRAND="#C3839D",
        TEXT="#B6BAC2",
        SECONDARY="#A6A6A6",
        DIM="#6E6E6E",
        WARNING="#E0B466",
        ERROR="#CF6B63",
        BG="#15161A",
        # Lifted plate for the composer (Droid/Claude/Cursor-style). Must read
        # clearly against BG — the old #1C1D22 was nearly invisible.
        INPUT_SURFACE="#252830",
    ),
    "purple": CliTheme(
        name="purple",
        HIGHLIGHT="#CCB7F0",
        BRAND="#9885B3",
        TEXT="#D0D0D0",
        SECONDARY="#B4B4BC",
        DIM="#6E6E6E",
        WARNING="#D8B06F",
        ERROR="#CF6B63",
        BG="#15161A",
        # Lifted plate for the composer (Droid/Claude/Cursor-style). Must read
        # clearly against BG — the old #1C1D22 was nearly invisible.
        INPUT_SURFACE="#252830",
    ),
    "orange": CliTheme(
        name="orange",
        HIGHLIGHT="#EBC29E",
        BRAND="#BC8A62",
        TEXT="#B6BAC2",
        SECONDARY="#A6A6A6",
        DIM="#6E6E6E",
        WARNING="#E0B466",
        ERROR="#CF6B63",
        BG="#15161A",
        # Lifted plate for the composer (Droid/Claude/Cursor-style). Must read
        # clearly against BG — the old #1C1D22 was nearly invisible.
        INPUT_SURFACE="#252830",
    ),
    "teal": CliTheme(
        name="teal",
        HIGHLIGHT="#8AE2D6",
        BRAND="#5BA89D",
        TEXT="#B6BAC2",
        SECONDARY="#A6A6A6",
        DIM="#6E6E6E",
        WARNING="#CEA25C",
        ERROR="#C45B52",
        BG="#15161A",
        # Lifted plate for the composer (Droid/Claude/Cursor-style). Must read
        # clearly against BG — the old #1C1D22 was nearly invisible.
        INPUT_SURFACE="#252830",
    ),
    "lime": CliTheme(
        name="lime",
        HIGHLIGHT="#D4FF7A",
        BRAND="#94C845",
        TEXT="#B6BAC2",
        SECONDARY="#A6A6A6",
        DIM="#6E6E6E",
        WARNING="#CEA25C",
        ERROR="#C45B52",
        BG="#15161A",
        # Lifted plate for the composer (Droid/Claude/Cursor-style). Must read
        # clearly against BG — the old #1C1D22 was nearly invisible.
        INPUT_SURFACE="#252830",
    ),
    "nord": CliTheme(
        name="nord",
        HIGHLIGHT="#88C0D0",
        BRAND="#81A1C1",
        TEXT="#E5E9F0",
        SECONDARY="#ACB5C2",
        DIM="#757D8C",
        WARNING="#EBCB8B",
        ERROR="#BF616A",
        BG="#2E3440",
        INPUT_SURFACE="#3B4252",
    ),
    "dracula": CliTheme(
        name="dracula",
        HIGHLIGHT="#BD93F9",
        BRAND="#8BE9FD",
        TEXT="#F8F8F2",
        SECONDARY="#B4B7C5",
        DIM="#6272A4",
        WARNING="#F1FA8C",
        ERROR="#FF5555",
        BG="#282A36",
        INPUT_SURFACE="#303443",
    ),
    "solarized": CliTheme(
        name="solarized",
        HIGHLIGHT="#2AA198",
        BRAND="#268BD2",
        TEXT="#EEE8D5",
        SECONDARY="#99A7A7",
        DIM="#5F747B",
        WARNING="#B58900",
        ERROR="#DC322F",
        BG="#002B36",
        INPUT_SURFACE="#073642",
    ),
    "gruvbox": CliTheme(
        name="gruvbox",
        HIGHLIGHT="#FABD2F",
        BRAND="#83A598",
        TEXT="#EBDBB2",
        SECONDARY="#B2A492",
        DIM="#787069",
        WARNING="#FE8019",
        ERROR="#FB4934",
        BG="#282828",
        INPUT_SURFACE="#32302F",
    ),
    "webflux": CliTheme(
        name="webflux",
        HIGHLIGHT="#E23636",
        BRAND="#2B63F5",
        TEXT="#EAF0FF",
        SECONDARY="#9CA8C6",
        DIM="#566282",
        WARNING="#FFC857",
        ERROR="#FF4D4D",
        BG="#0D1220",
        INPUT_SURFACE="#151D33",
    ),
    "sunset": CliTheme(
        name="sunset",
        HIGHLIGHT="#FFB067",
        BRAND="#FF6FA8",
        TEXT="#FFE8D5",
        SECONDARY="#C8A79B",
        DIM="#7B605C",
        WARNING="#FFD166",
        ERROR="#FF5A5F",
        BG="#22151A",
        INPUT_SURFACE="#2B1C24",
    ),
}


def _fg(rgb: tuple[int, int, int]) -> str:
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def _mix_rgb(
    start: tuple[int, int, int],
    end: tuple[int, int, int],
    amount: float,
) -> tuple[int, int, int]:
    clamped = 0.0 if amount < 0.0 else 1.0 if amount > 1.0 else amount
    return (
        int(start[0] + (end[0] - start[0]) * clamped),
        int(start[1] + (end[1] - start[1]) * clamped),
        int(start[2] + (end[2] - start[2]) * clamped),
    )


def _sample_cyclic_gradient(
    stops: tuple[tuple[int, int, int], ...],
    position: float,
) -> tuple[int, int, int]:
    scaled = (position % 1.0) * len(stops)
    index = int(scaled)
    return _mix_rgb(stops[index], stops[(index + 1) % len(stops)], scaled - index)


def fade_fg_ansi(intensity: float) -> str:
    """Foreground between DIM and TEXT.

    ``intensity`` is 0 (DIM) to 1 (TEXT). Live-action glow uses this so
    runtime code never builds a raw truecolor escape.
    """
    clamped = 0.0 if intensity < 0.0 else 1.0 if intensity > 1.0 else intensity
    dim = _parse_hex_color(_ACTIVE_THEME.DIM)
    text = _parse_hex_color(_ACTIVE_THEME.TEXT)
    return _fg(_mix_rgb(dim, text, clamped))


def shimmer_text_ansi(
    text: str,
    *,
    elapsed: float,
    period: float = 1.5,
    high_hex: str | None = None,
) -> str:
    """Paint *text* with a traveling metallic, iridescent wave.

    Brightness is a pure function of *elapsed* and character index so prompt
    re-measure passes at the same clock stay visually stable. Whitespace is
    left unstyled so the wave reads as light over the words, not the gaps. The
    narrow accent reflections are softened with TEXT to avoid a saturated
    rainbow while neutral TEXT and SECONDARY stops provide a silver sheen.
    """
    if not text:
        return ""
    dim = _parse_hex_color(_ACTIVE_THEME.DIM)
    secondary = _parse_hex_color(_ACTIVE_THEME.SECONDARY)
    text_rgb = _parse_hex_color(_ACTIVE_THEME.TEXT)
    accent = _parse_hex_color(high_hex or _ACTIVE_THEME.HIGHLIGHT)
    brand = _parse_hex_color(_ACTIVE_THEME.BRAND)
    stops = (
        dim,
        secondary,
        _mix_rgb(text_rgb, brand, 0.38),
        text_rgb,
        _mix_rgb(text_rgb, accent, 0.52),
        text_rgb,
        secondary,
        dim,
    )
    span = max(period, 0.05)
    wave = (elapsed / span) % 1.0
    n = len(text)
    parts: list[str] = []
    for index, char in enumerate(text):
        if char.isspace():
            parts.append(char)
            continue
        pos = index / max(n - 1, 1)
        rgb = _sample_cyclic_gradient(stops, pos - wave)
        parts.append(f"{_fg(rgb)}{char}")
    parts.append(ANSI_RESET)
    return "".join(parts)


def _parse_hex_color(value: str) -> tuple[int, int, int]:
    stripped = value.lstrip("#")
    return (int(stripped[0:2], 16), int(stripped[2:4], 16), int(stripped[4:6], 16))


def reply_marker_hex() -> str:
    """Hex for the assistant ``Ω`` / tool ``⏺`` accent — the active theme's
    ``HIGHLIGHT``, so every component follows the selected palette rather than a
    fixed colour that would read as a copy of another tool."""
    return _ACTIVE_THEME.HIGHLIGHT


def reply_marker_style() -> str:
    """Bold tool-lead accent using the reply marker's active highlight colour."""
    return f"bold {reply_marker_hex()}"


class _LazyRichStyle(str):
    """Rich markup colour token that tracks :func:`set_active_theme`.

    Importers can bind ``HIGHLIGHT`` (etc.) at module load; ``str()`` and
    ``f"[{HIGHLIGHT}]"`` resolve against the active palette at render time.
    """

    __slots__ = ("_field", "_bold")

    def __new__(cls, field: str, *, bold: bool = False) -> _LazyRichStyle:
        instance = str.__new__(cls, "")
        object.__setattr__(instance, "_field", field)
        object.__setattr__(instance, "_bold", bold)
        return instance

    def _resolve(self) -> str:
        field = object.__getattribute__(self, "_field")
        bold = object.__getattribute__(self, "_bold")
        value = getattr(_ACTIVE_THEME, field)
        return f"bold {value}" if bold else value

    def __str__(self) -> str:
        return self._resolve()

    def __format__(self, format_spec: str) -> str:
        return format(self._resolve(), format_spec)

    def __bool__(self) -> bool:
        return bool(self._resolve())

    # Hash/compare as the resolved style string. The underlying str value is
    # "" for every token, so without these overrides all tokens collide on
    # the same key in caches keyed by style strings — Rich's lru_cached
    # ``Style.parse`` then renders every ``style=TOKEN`` usage with whichever
    # token happened to be parsed first.
    def __eq__(self, other: object) -> bool:
        return self._resolve() == other

    def __ne__(self, other: object) -> bool:
        return self._resolve() != other

    def __hash__(self) -> int:
        return hash(self._resolve())

    def lstrip(self, chars: str | None = None) -> str:
        resolved = self._resolve()
        return resolved.lstrip() if chars is None else resolved.lstrip(chars)

    def rstrip(self, chars: str | None = None) -> str:
        resolved = self._resolve()
        return resolved.rstrip() if chars is None else resolved.rstrip(chars)

    def strip(self, chars: str | None = None) -> str:
        resolved = self._resolve()
        return resolved.strip() if chars is None else resolved.strip(chars)

    def split(self, sep: str | None = None, maxsplit: SupportsIndex = -1) -> list[str]:
        resolved = self._resolve()
        return resolved.split(sep, maxsplit)

    def rsplit(self, sep: str | None = None, maxsplit: SupportsIndex = -1) -> list[str]:
        resolved = self._resolve()
        return resolved.rsplit(sep, maxsplit)


def _resolve_theme_name(name: str | None) -> str:
    normalized = (name or DEFAULT_THEME_NAME).strip().lower()
    return normalized if normalized in THEME_REGISTRY else DEFAULT_THEME_NAME


def get_theme(theme_name: str | None) -> CliTheme:
    """Return a registered palette by name; fall back to default."""
    return THEME_REGISTRY[_resolve_theme_name(theme_name)]


def list_theme_names() -> tuple[str, ...]:
    """Return available theme names in display order."""
    return THEME_NAMES


def get_active_theme() -> CliTheme:
    """Return the currently active palette."""
    return _ACTIVE_THEME


def get_active_theme_name() -> str:
    """Return the currently active palette name."""
    return _ACTIVE_THEME.name


def _apply_theme(theme: CliTheme) -> None:
    global HIGHLIGHT_ANSI, BRAND_ANSI, TEXT_ANSI, SECONDARY_ANSI, DIM_ANSI, BOLD_BRAND_ANSI
    global PROMPT_ACCENT_ANSI, PROMPT_FRAME_ANSI, DIM_COUNTER_ANSI, SURFACE_BG_ANSI
    global INPUT_SURFACE_BG_ANSI, MENU_SELECTION_ROW_ANSI, MARKDOWN_THEME
    global DEVICE_CODE_ANSI, REPLY_MARKER_ANSI, BOLD_REPLY_MARKER_ANSI

    _highlight_rgb = _parse_hex_color(theme.HIGHLIGHT)
    _brand_rgb = _parse_hex_color(theme.BRAND)
    _text_rgb = _parse_hex_color(theme.TEXT)
    _secondary_rgb = _parse_hex_color(theme.SECONDARY)
    _dim_rgb = _parse_hex_color(theme.DIM)
    _bg_rgb = _parse_hex_color(theme.BG)
    _input_surface_rgb = _parse_hex_color(theme.INPUT_SURFACE)
    _reply_rgb = _parse_hex_color(theme.HIGHLIGHT)

    HIGHLIGHT_ANSI = _fg(_highlight_rgb)
    BRAND_ANSI = _fg(_brand_rgb)
    TEXT_ANSI = _fg(_text_rgb)
    SECONDARY_ANSI = _fg(_secondary_rgb)
    DIM_ANSI = _fg(_dim_rgb)
    BOLD_BRAND_ANSI = f"\x1b[1m{BRAND_ANSI}"
    REPLY_MARKER_ANSI = _fg(_reply_rgb)
    BOLD_REPLY_MARKER_ANSI = f"\x1b[1m{REPLY_MARKER_ANSI}"

    PROMPT_ACCENT_ANSI = f"\x1b[1;38;2;{_highlight_rgb[0]};{_highlight_rgb[1]};{_highlight_rgb[2]}m"
    PROMPT_FRAME_ANSI = PROMPT_ACCENT_ANSI
    DEVICE_CODE_ANSI = PROMPT_ACCENT_ANSI
    DIM_COUNTER_ANSI = DIM_ANSI
    SURFACE_BG_ANSI = f"\x1b[48;2;{_bg_rgb[0]};{_bg_rgb[1]};{_bg_rgb[2]}m"
    INPUT_SURFACE_BG_ANSI = (
        f"\x1b[48;2;{_input_surface_rgb[0]};{_input_surface_rgb[1]};{_input_surface_rgb[2]}m"
    )
    MENU_SELECTION_ROW_ANSI = f"{INPUT_SURFACE_BG_ANSI}\x1b[1m{HIGHLIGHT_ANSI}"

    MARKDOWN_THEME = Theme(
        {
            # Bright warm-grey body with the accent reserved for code spans; bold
            # marks headings, strong text and table headers only, so structure
            # stays visible when a reply is mostly file names and flags.
            "markdown.paragraph": theme.TEXT,
            "markdown.code": theme.HIGHLIGHT,
            "markdown.code_block": theme.TEXT,
            "markdown.h1": f"bold {theme.HIGHLIGHT}",
            "markdown.h2": f"bold {theme.WARNING}",
            "markdown.h3": f"bold {theme.TEXT}",
            "markdown.h4": f"bold {theme.SECONDARY}",
            "markdown.strong": f"bold {theme.TEXT}",
            "markdown.em": f"italic {theme.SECONDARY}",
            "markdown.item.bullet": f"bold {theme.WARNING}",
            "markdown.item.number": f"bold {theme.WARNING}",
            "markdown.block_quote": theme.SECONDARY,
            "markdown.link": f"underline {theme.HIGHLIGHT}",
            "markdown.link_url": theme.DIM,
            "markdown.hr": theme.DIM,
            "markdown.table.header": f"bold {theme.HIGHLIGHT}",
            "markdown.table.border": theme.DIM,
        }
    )


def set_active_theme(theme_name: str | None) -> CliTheme:
    """Activate a palette and refresh all derived style constants."""
    global _ACTIVE_THEME
    _ACTIVE_THEME = get_theme(theme_name)
    _apply_theme(_ACTIVE_THEME)
    return _ACTIVE_THEME


# ── Semantic color tokens (the only permitted colours) ─────────────────────
_ACTIVE_THEME = get_theme(DEFAULT_THEME_NAME)

HIGHLIGHT = _LazyRichStyle("HIGHLIGHT")
BRAND = _LazyRichStyle("BRAND")
TEXT = _LazyRichStyle("TEXT")
SECONDARY = _LazyRichStyle("SECONDARY")
DIM = _LazyRichStyle("DIM")
WARNING = _LazyRichStyle("WARNING")
ERROR = _LazyRichStyle("ERROR")
BG = _LazyRichStyle("BG")
INPUT_SURFACE = _LazyRichStyle("INPUT_SURFACE")

# ── Rich style shorthands (bold variants of the semantic tokens) ──────────

BOLD_HIGHLIGHT = _LazyRichStyle("HIGHLIGHT", bold=True)
BOLD_BRAND = _LazyRichStyle("BRAND", bold=True)
BOLD_TEXT = _LazyRichStyle("TEXT", bold=True)
BOLD_WARNING = _LazyRichStyle("WARNING", bold=True)
BOLD_ERROR = _LazyRichStyle("ERROR", bold=True)

# Skill loading should remain recognizable regardless of the selected accent
# theme. Reuse the canonical green palette rather than defining a UI-local
# colour.
BOLD_SKILL = f"bold {THEME_REGISTRY['green'].BRAND}"

# Pygments style used for fenced code blocks in every Rich Markdown render.
MARKDOWN_CODE_THEME = "ansi_dark"

# GitHub/device-flow one-time codes should be easy to spot and transcribe.
DEVICE_CODE = BOLD_HIGHLIGHT

# Distinct accent for incoming alerts (visually distinct from BOLD_BRAND used for assistant)
INCOMING_ALERT_ACCENT = BOLD_WARNING

__all__ = [
    "ANSI_BOLD",
    "ANSI_DIM",
    "ANSI_RESET",
    "BG",
    "BOLD_BRAND",
    "BOLD_BRAND_ANSI",
    "BOLD_ERROR",
    "BOLD_HIGHLIGHT",
    "BOLD_SKILL",
    "BOLD_TEXT",
    "BOLD_WARNING",
    "BRAND",
    "BRAND_ANSI",
    "DEVICE_CODE",
    "DEVICE_CODE_ANSI",
    "DIM",
    "DIM_ANSI",
    "DIM_COUNTER_ANSI",
    "ERROR",
    "fade_fg_ansi",
    "shimmer_text_ansi",
    "GLYPH_ACTIVE",
    "GLYPH_BULLET",
    "GLYPH_ERROR",
    "GLYPH_PROMPT",
    "GLYPH_SUCCESS",
    "GLYPH_WARNING",
    "HIGHLIGHT",
    "HIGHLIGHT_ANSI",
    "INCOMING_ALERT_ACCENT",
    "INPUT_SURFACE",
    "INPUT_SURFACE_BG_ANSI",
    "MARKDOWN_CODE_THEME",
    "MARKDOWN_THEME",
    "MENU_SELECTION_ROW_ANSI",
    "PROMPT_ACCENT_ANSI",
    "PROMPT_FRAME_ANSI",
    "REPLY_MARKER_ANSI",
    "BOLD_REPLY_MARKER_ANSI",
    "SECONDARY",
    "SECONDARY_ANSI",
    "SURFACE_BG_ANSI",
    "TEXT",
    "TEXT_ANSI",
    "WARNING",
    "reply_marker_hex",
    "reply_marker_style",
]

# ── Semantic glyphs ────────────────────────────────────────────────────────

GLYPH_SUCCESS = "✓"
GLYPH_WARNING = "⚠"
GLYPH_ERROR = "✗"
GLYPH_PROMPT = "◆"
GLYPH_ACTIVE = "◉"
GLYPH_BULLET = "·"

# ── ANSI escape sequences for prompt_toolkit (bypasses Rich markup) ────────
# This module is the only place in the codebase where raw ANSI escapes are
# permitted. Every truecolour value below corresponds to one of the eight
# semantic tokens above.

# Placeholders — populated by :func:`set_active_theme` (ANSI + Markdown only).
HIGHLIGHT_ANSI = ""
BRAND_ANSI = ""
TEXT_ANSI = ""
SECONDARY_ANSI = ""
DIM_ANSI = ""
BOLD_BRAND_ANSI = ""
REPLY_MARKER_ANSI = ""
BOLD_REPLY_MARKER_ANSI = ""
DEVICE_CODE_ANSI = ""

ANSI_RESET = "\x1b[0m"
ANSI_BOLD = "\x1b[1m"
ANSI_DIM = "\x1b[2m"

PROMPT_ACCENT_ANSI = ""
PROMPT_FRAME_ANSI = ""
DIM_COUNTER_ANSI = ""
SURFACE_BG_ANSI = ""
INPUT_SURFACE_BG_ANSI = ""
MENU_SELECTION_ROW_ANSI = ""

MARKDOWN_THEME = Theme({})

# Ensure ANSI/Markdown derived constants match the default active theme.
set_active_theme(DEFAULT_THEME_NAME)

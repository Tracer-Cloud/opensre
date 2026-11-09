"""ASCII splash art and art-selection logic for the startup screen.

Exported
--------
SPLASH_ART          block font, 59 cols, solid ██ fills
SPLASH_ART_NARROW   simpleBlock font, 72 cols, pure ASCII fallback
_FALLBACK_ART       minimal art, 44 cols, last resort
render_art(width)  return the best-fit art string for a given terminal width
"""

from __future__ import annotations

import os

from infrastructure.observability.render.figlet import render_figlet

# Pre-rendered during development and checked into this module as a static string.
# Colour codes are stripped; HIGHLIGHT is re-applied at render time.
SPLASH_ART = """\
 ██████╗ ██████╗ ███████╗███╗   ██╗███████╗██████╗ ███████╗
██╔═══██╗██╔══██╗██╔════╝████╗  ██║██╔════╝██╔══██╗██╔════╝
██║   ██║██████╔╝█████╗  ██╔██╗ ██║███████╗██████╔╝█████╗
██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║╚════██║██╔══██╗██╔══╝
╚██████╔╝██║     ███████╗██║ ╚████║███████║██║  ██║███████╗
 ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝╚══════╝"""

SPLASH_ART_NARROW = """\
    _|_|    _|_|_|    _|_|_|_|  _|      _|    _|_|_|  _|_|_|    _|_|_|_|
  _|    _|  _|    _|  _|        _|_|    _|  _|        _|    _|  _|
  _|    _|  _|_|_|    _|_|_|    _|  _|  _|    _|_|    _|_|_|    _|_|_|
  _|    _|  _|        _|        _|    _|_|        _|  _|    _|  _|
    _|_|    _|        _|_|_|_|  _|      _|  _|_|_|    _|    _|  _|_|_|_|"""

_FALLBACK_ART = """\
  ___                    ____  ____  _____
 / _ \\ _ __   ___ _ __  / ___||  _ \\| ____|
| | | | '_ \\ / _ \\ '_ \\ \\___ \\| |_) |  _|
| |_| | |_) |  __/ | | | ___) |  _ <| |___
 \\___/| .__/ \\___|_| |_||____/|_| \\_\\_____|
      |_|"""


def render_art(console_width: int = 80) -> str:
    """Return the splash art string for the given terminal width.

    Priority: SPLASH_ART (grid, 34 cols) → SPLASH_ART_NARROW (simpleBlock, 72 cols)
    → _FALLBACK_ART (minimal, 44 cols).  OPENSRE_FIGLET_FONT overrides the default
    when pyfiglet is installed.
    """
    custom_font = os.getenv("OPENSRE_FIGLET_FONT")
    if custom_font:
        rendered = render_figlet("OpenSRE", font=custom_font, max_line_width=console_width - 2)
        if rendered:
            return rendered

    art_width = max(len(ln) for ln in SPLASH_ART.splitlines())
    narrow_width = max(len(ln) for ln in SPLASH_ART_NARROW.splitlines())
    fallback_width = max(len(ln) for ln in _FALLBACK_ART.splitlines())

    if console_width >= art_width + 4:
        return SPLASH_ART
    if console_width >= narrow_width + 4:
        return SPLASH_ART_NARROW
    if console_width >= fallback_width + 4:
        return _FALLBACK_ART
    return _FALLBACK_ART


__all__ = [
    "SPLASH_ART",
    "SPLASH_ART_NARROW",
    "_FALLBACK_ART",
    "render_art",
]

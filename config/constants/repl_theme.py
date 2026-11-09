"""Interactive-shell theme vocabulary: which palette names exist and the default.

The hex palettes keyed by these names live in ``infrastructure.terminal.theme``;
config owns only the name set so the leaf config tier can validate a requested
theme without importing the rendering tier.
"""

from __future__ import annotations

from enum import StrEnum


class Theme(StrEnum):
    """Available interactive-shell palette names, in display order.

    The palette registry must key on exactly these members (pinned by the theme
    conformance test).
    """

    GREEN = "green"
    BLUE = "blue"
    AMBER = "amber"
    MONO = "mono"
    RED = "red"
    PINK = "pink"
    PURPLE = "purple"
    ORANGE = "orange"
    TEAL = "teal"
    LIME = "lime"
    NORD = "nord"
    DRACULA = "dracula"
    SOLARIZED = "solarized"
    GRUVBOX = "gruvbox"
    WEBFLUX = "webflux"
    SUNSET = "sunset"


#: Theme applied when none is configured or an unknown name is requested.
# Purple is OpenSRE's own identity — deliberately not the warm gold/orange chrome
# that reads as a Factory-droid copycat. The ``amber`` and ``orange`` palettes
# stay available (``/theme``) for side-by-side visual comparison.
DEFAULT_THEME_NAME = Theme.PURPLE

#: Theme names in display order (a plain tuple for callers that iterate names).
THEME_NAMES: tuple[Theme, ...] = tuple(Theme)

__all__ = ["DEFAULT_THEME_NAME", "THEME_NAMES", "Theme"]

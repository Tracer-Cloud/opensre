from __future__ import annotations

from infrastructure.terminal.theme import BG, HIGHLIGHT, set_active_theme
from surfaces.cli.wizard import components as wizard_ui


def test_questionary_highlighted_style_uses_dark_text_on_highlight_background() -> None:
    set_active_theme("green")
    style = wizard_ui._questionary_style()
    highlighted = next(rule for rule in style.style_rules if rule[0] == "highlighted")
    assert highlighted[1] == f"fg:{BG} bg:{HIGHLIGHT} bold"

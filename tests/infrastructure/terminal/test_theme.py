"""Tests for the lazy semantic color tokens in infrastructure.terminal.theme."""

from __future__ import annotations

import re

from rich.console import Console
from rich.text import Text

from infrastructure.terminal.theme import (
    BRAND,
    DIM,
    SECONDARY,
    TEXT,
    get_theme,
    list_theme_names,
    set_active_theme,
)


def test_tokens_hash_and_compare_as_resolved_style() -> None:
    """Tokens must never collide on their (empty) underlying str value.

    Rich's ``Style.parse`` is lru_cached on the style string; when every token
    hashed as ``""`` they all shared one cache entry, so ``style=TOKEN`` always
    rendered as whichever token was parsed first in the process.
    """
    set_active_theme("blue")
    theme = get_theme("blue")

    assert SECONDARY == theme.SECONDARY
    assert DIM == theme.DIM
    assert SECONDARY != DIM
    assert hash(SECONDARY) == hash(theme.SECONDARY)
    assert hash(SECONDARY) != hash(DIM)
    assert len({SECONDARY, DIM, BRAND, TEXT}) == 4


def test_rich_renders_each_token_with_its_own_color() -> None:
    """End-to-end: distinct tokens produce distinct truecolor escapes."""
    set_active_theme("blue")
    console = Console(
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
        no_color=False,
    )

    text = Text()
    text.append("a", style=SECONDARY)
    text.append("b", style=DIM)
    text.append("c", style=BRAND)
    with console.capture() as capture:
        console.print(text)

    output = capture.get()
    for token in (SECONDARY, DIM, BRAND):
        red, green, blue = (int(str(token).lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
        assert f"38;2;{red};{green};{blue}m" in output


def test_muted_tokens_are_readable_on_theme_background() -> None:
    """SECONDARY and DIM must keep minimum contrast against the theme BG.

    Regression for the near-invisible onboarding text: DIM #444444 on the
    #0A0A0A background was ~2:1 contrast.
    """

    def _luminance(hex_color: str) -> float:
        def channel(value: int) -> float:
            scaled = value / 255
            return scaled / 12.92 if scaled <= 0.04045 else ((scaled + 0.055) / 1.055) ** 2.4

        stripped = hex_color.lstrip("#")
        red, green, blue = (int(stripped[i : i + 2], 16) for i in (0, 2, 4))
        return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)

    def _contrast(foreground: str, background: str) -> float:
        lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
        return (lighter + 0.05) / (darker + 0.05)

    for name in list_theme_names():
        theme = get_theme(name)
        assert _contrast(theme.SECONDARY, theme.BG) >= 6.0, name
        assert _contrast(theme.DIM, theme.BG) >= 3.0, name


def test_fade_fg_ansi_interpolates_dim_to_text() -> None:
    """Live-action glow must come from theme tokens, not a raw RGB escape."""
    from infrastructure.terminal import theme as ui_theme

    ui_theme.set_active_theme("solarized")
    assert ui_theme.fade_fg_ansi(0.0) == ui_theme.DIM_ANSI
    assert ui_theme.fade_fg_ansi(1.0) == ui_theme.TEXT_ANSI
    assert ui_theme.fade_fg_ansi(0.0) != ui_theme.fade_fg_ansi(1.0)
    mid = ui_theme.fade_fg_ansi(0.5)
    assert mid.startswith("\x1b[38;2;")
    assert mid != ui_theme.DIM_ANSI
    assert mid != ui_theme.TEXT_ANSI


def test_shimmer_text_ansi_paints_a_traveling_metallic_wave() -> None:
    """Status shimmer mixes neutral silver stops with muted accent reflections."""
    from infrastructure.terminal import theme as ui_theme

    ui_theme.set_active_theme("blue")
    early = ui_theme.shimmer_text_ansi("Thinking…", elapsed=0.0)
    later = ui_theme.shimmer_text_ansi("Thinking…", elapsed=0.75)
    assert early.count("\x1b[38;2;") >= 5
    assert early.endswith(ui_theme.ANSI_RESET)
    assert early != later
    assert ui_theme.TEXT_ANSI in early
    assert ui_theme.SECONDARY_ANSI in early
    colors = re.findall(r"\x1b\[38;2;(\d+);(\d+);(\d+)m", early)
    assert len(set(colors)) >= 5

    highlight_wave = ui_theme.shimmer_text_ansi(
        "Thinking…",
        elapsed=0.0,
        high_hex=ui_theme.get_active_theme().HIGHLIGHT,
    )
    brand_wave = ui_theme.shimmer_text_ansi(
        "Thinking…",
        elapsed=0.0,
        high_hex=ui_theme.get_active_theme().BRAND,
    )
    assert highlight_wave != brand_wave

    # Whitespace is not individually coloured.
    spaced = ui_theme.shimmer_text_ansi("Invoking tools", elapsed=0.1)
    assert " tools" in spaced or "tools" in re.sub(r"\x1b\[[0-9;]*m", "", spaced)


def test_tool_marker_follows_the_active_theme_highlight() -> None:
    """The bold tool marker follows each palette's highlight colour."""
    from infrastructure.terminal import theme as ui_theme

    for name in ("blue", "purple", "green", "mono"):
        ui_theme.set_active_theme(name)
        assert ui_theme.reply_marker_style() == f"bold {ui_theme.get_theme(name).HIGHLIGHT}"


def test_reply_block_paints_orange_marker_and_themed_body() -> None:
    """Regression: marker washed out when body fell through to terminal white."""
    import os

    from surfaces.interactive_shell.ui.streaming.renderer import (
        _reply_marker_style,
        render_reply_block,
    )

    os.environ.pop("NO_COLOR", None)
    set_active_theme("blue")
    console = Console(
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
        no_color=False,
    )
    with console.capture() as capture:
        render_reply_block(console, "Hey! How can I help?")
    output = capture.get()
    assert "Ω" in output
    assert _reply_marker_style() == f"bold {get_theme('blue').HIGHLIGHT}"
    # Marker follows the active theme's HIGHLIGHT (whatever the palette sets).
    highlight = get_theme("blue").HIGHLIGHT.lstrip("#")
    r, g, b = (int(highlight[i : i + 2], 16) for i in (0, 2, 4))
    assert f"38;2;{r};{g};{b}m" in output
    # Body uses the sunny Droid-like agent grey (#D0D0D0).
    assert "38;2;208;208;208m" in output


def test_palette_registry_keys_match_the_config_vocabulary() -> None:
    """The infra palettes must cover exactly the config-owned theme names.

    ``config.constants.repl_theme.THEME_NAMES`` is the leaf-tier vocabulary the
    config validator trusts. A palette added or removed here without updating
    that tuple would let config accept a name with no palette, or reject one
    that renders — this pins them to the same set and order.
    """
    from config.constants.repl_theme import THEME_NAMES
    from infrastructure.terminal.theme import THEME_REGISTRY

    assert tuple(THEME_REGISTRY.keys()) == THEME_NAMES


def test_markdown_theme_reserves_bold_for_structure() -> None:
    # Arrange
    from infrastructure.terminal import theme as ui_theme

    styles = ui_theme.MARKDOWN_THEME.styles

    # Act / Assert: code spans keep their colour without bold; table header is
    # bold and the border is dim, so the body of a table never outweighs it.
    assert styles["markdown.code"].bold is not True
    assert styles["markdown.table.header"].bold is True
    assert styles["markdown.table.border"] == styles["markdown.hr"]

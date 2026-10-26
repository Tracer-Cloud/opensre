"""Prompt-region width and clipping."""

from __future__ import annotations

from surfaces.shared.terminal.prompt_layout import clip_prompt_text, prompt_text_width


def test_clip_prompt_text_limits_terminal_columns_not_codepoints() -> None:
    """CJK and emoji occupy two columns; clipping by ``len()`` would wrap."""
    clipped = clip_prompt_text("中" * 10, 10)
    assert clipped == "中中中中…"
    assert prompt_text_width(clipped) <= 10

    emoji = clip_prompt_text("👋" * 8, 7)
    assert emoji.endswith("…")
    assert prompt_text_width(emoji) <= 7

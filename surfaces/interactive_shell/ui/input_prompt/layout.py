"""Shared sizing and clipping helpers for prompt UI text."""

from __future__ import annotations

from surfaces.shared.terminal.prompt_layout import (
    clip_prompt_text,
    prompt_line_width,
    terminal_columns,
)

_COMPLETION_META_PADDING = 6
_COMPLETION_META_MIN_WIDTH = 24


def _completion_meta_width(command_name: str, cols: int) -> int:
    return max(_COMPLETION_META_MIN_WIDTH, cols - len(command_name) - _COMPLETION_META_PADDING)


def _short_meta(
    text: str,
    *,
    command_name: str = "",
    max_len: int | None = None,
    cols: int | None = None,
) -> str:
    if max_len is None:
        if command_name:
            max_len = _completion_meta_width(command_name, cols or terminal_columns())
        else:
            max_len = 54
    return clip_prompt_text(text, max_len)


__all__ = [
    "clip_prompt_text",
    "prompt_line_width",
]

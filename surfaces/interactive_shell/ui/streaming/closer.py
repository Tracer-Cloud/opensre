"""Flushing a deferred Want-me-to closer after gather normalizes the final answer."""

from __future__ import annotations

from rich.console import Console

from core.agent_harness.spi.prompt_chrome import closer_tail_from
from surfaces.interactive_shell.ui.streaming.renderer import render_markdown_block


def finish_deferred_closer(
    console: Console,
    final_text: str,
    *,
    footer_elapsed_s: float | None = None,
    footer_total_bytes: int | None = None,
) -> None:
    """Render the (possibly rewritten) Want-me-to closer."""
    del footer_elapsed_s, footer_total_bytes  # per-turn footer meta line removed
    closer = closer_tail_from(final_text)
    if closer:
        render_markdown_block(console, closer)
    console.print()

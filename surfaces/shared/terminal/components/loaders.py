"""Shared Rich loaders for interactive-shell LLM calls.

A quiet, dim spinner shows that an LLM call is in flight.  Centralised so
every LLM-backed surface in the interactive shell (``cli_agent``,
``follow_up``) shares the same look.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from rich.console import Console

from infrastructure.terminal.theme import SECONDARY

# Quiet, secondary-colour spinner — less visual noise than a bright accent.
_LOADER_COLOR = SECONDARY
_LOADER_SPINNER = "dots"

DEFAULT_LOADER_LABEL = "thinking"


@contextmanager
def llm_loader(console: Console, label: str = DEFAULT_LOADER_LABEL) -> Iterator[None]:
    """Show a dim spinner while an LLM call is in flight.

    On non-terminal consoles (CI, captured output, piped stdout), the spinner is
    skipped so captured logs stay clean — the wrapped call still runs unchanged.
    """
    if not console.is_terminal:
        yield
        return

    console.print()
    text = f"[{_LOADER_COLOR}]{label}…[/{_LOADER_COLOR}]"
    with console.status(text, spinner=_LOADER_SPINNER, spinner_style=_LOADER_COLOR):
        yield


__all__ = ["DEFAULT_LOADER_LABEL", "llm_loader"]

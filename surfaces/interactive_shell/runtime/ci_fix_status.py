"""Bind the local repair counter to the terminal's live status line."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from integrations.github import get_ci_fix_counter

if TYPE_CHECKING:
    from surfaces.interactive_shell.session.terminal_session import TerminalSession

logger = logging.getLogger(__name__)


def bind_ci_fix_status(terminal: TerminalSession) -> Callable[[], None]:
    """Attach a cached count and redraw listener for this shell's lifetime."""
    unsubscribe: Callable[[], None] | None = None
    try:
        counter = get_ci_fix_counter()
        counter.reload()
        terminal.ci_fix_count_fn = counter.count
        unsubscribe = counter.subscribe(terminal.notify_prompt_changed)
    except Exception:
        logger.debug("CI fix status unavailable", exc_info=True)
        terminal.ci_fix_count_fn = lambda: 0

    def cleanup() -> None:
        if unsubscribe is not None:
            unsubscribe()
        terminal.ci_fix_count_fn = None

    return cleanup

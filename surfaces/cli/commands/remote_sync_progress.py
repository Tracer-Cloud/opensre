"""Click progress-bar rendering for ``opensre remote-sync sync``."""

from __future__ import annotations

from typing import Any

import click

from infrastructure.filestorage.engine import SyncProgress
from infrastructure.filestorage.enums import SyncDirection
from infrastructure.filestorage.messages import direction_label, sanitize_terminal_text


class CliProgress:
    """Renders each direction's :class:`SyncProgress` events as a Click bar.

    ``run_sync`` always finishes pull before starting push, so at most one
    bar is open at a time; a direction change closes the previous bar (if
    any) and opens the next with its own known length.
    """

    def __init__(self) -> None:
        self._bar: Any = None
        self._direction: SyncDirection | None = None

    def __call__(self, progress: SyncProgress) -> None:
        if progress.direction is not self._direction:
            self.close()
            self._bar = click.progressbar(
                length=progress.total,
                label=direction_label(progress.direction),
                item_show_func=lambda item: item,
            )
            self._bar.__enter__()
            self._direction = progress.direction
        self._bar.current_item = sanitize_terminal_text(progress.key)
        self._bar.update(1)

    def close(self) -> None:
        if self._bar is not None:
            self._bar.__exit__(None, None, None)
            self._bar = None
            self._direction = None


__all__ = ["CliProgress"]

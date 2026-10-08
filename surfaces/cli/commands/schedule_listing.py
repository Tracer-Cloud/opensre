"""Load run summaries for the CLI's schedule lists."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence

from rich.console import Console
from rich.text import Text

from infrastructure.scheduling.scheduler.loop_results import latest_loop_runs
from infrastructure.scheduling.scheduler.loops import LoopSummary
from infrastructure.terminal.theme import WARNING
from surfaces.shared.terminal.tables.schedules import print_schedules

_logger = logging.getLogger(__name__)


def print_loop_schedules(
    console: Console,
    loops: Sequence[LoopSummary],
) -> None:
    """Keep schedules inspectable even when retained run history is unavailable."""
    history_available = True
    try:
        latest = latest_loop_runs(loops)
    except (OSError, sqlite3.Error):
        _logger.warning("Could not read schedule run history", exc_info=True)
        latest = {}
        history_available = False
        console.print(
            Text("Run history unavailable; showing schedule configuration.", style=WARNING)
        )
    print_schedules(console, loops, latest, history_available=history_available)

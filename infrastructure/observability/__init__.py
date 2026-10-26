"""Observability ports — abstractions core code uses to report progress,
debug output, and check the runtime output format.

Core agent/pipeline/utils code depends only on these ports. Concrete
implementations live in adapter packages (e.g. the Rich-backed REPL
tracker under ``surfaces/shared/terminal/output/``). The boundary
(typically the CLI entry point) registers the adapter via the
``set_progress_tracker`` / ``set_debug_printer`` injection helpers.

This is the same Ports & Adapters pattern used by upstream correlation
provider wiring:
high-level modules depend on abstractions, not concretions.
"""

from __future__ import annotations

from infrastructure.observability.render.debug import debug_print, set_debug_printer
from infrastructure.observability.render.output_format import get_output_format
from infrastructure.observability.render.progress import (
    NoopProgressTracker,
    ProgressReporter,
    get_progress_tracker,
    set_progress_tracker,
    set_progress_tracker_factory,
    silence_progress_tracker,
)

__all__ = [
    "NoopProgressTracker",
    "ProgressReporter",
    "debug_print",
    "get_output_format",
    "get_progress_tracker",
    "set_debug_printer",
    "set_progress_tracker",
    "set_progress_tracker_factory",
    "silence_progress_tracker",
]

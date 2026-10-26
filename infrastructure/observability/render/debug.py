"""Debug-print port — plain default + injection for richer adapters.

Core call sites do ``debug_print("...")`` unconditionally. The default
implementation prints to stdout only when ``TRACER_VERBOSE`` is set,
matching how the legacy CLI helper behaved in non-rich mode.

The REPL boundary can register a Rich-styled adapter via
:func:`set_debug_printer` so debug output threads through the
persistent input frame instead of landing as raw text below it.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

DebugPrinter = Callable[[str], None]


def verbose_output_enabled() -> bool:
    """True iff ``TRACER_VERBOSE`` indicates the user wants debug output.

    Kept narrow on purpose: the legacy helper also consulted the
    interactive-shell's data-store (``is_debug``/``is_verbose``); pulling
    that in would re-introduce the CLI dependency we're refactoring
    out of core. Adapters that want richer gating can register their
    own printer that checks additional state.
    """
    return os.getenv("TRACER_VERBOSE", "").lower() in ("1", "true", "yes")


def _default_debug_printer(message: str) -> None:
    if not verbose_output_enabled():
        return
    print(f"DEBUG: {message}", file=sys.stderr)


_printer: DebugPrinter = _default_debug_printer


def debug_print(message: str) -> None:
    """Emit a debug message via the registered printer."""
    _printer(message)


def set_debug_printer(printer: DebugPrinter) -> None:
    """Install ``printer`` as the active debug-print implementation.

    Boundary code (typically the CLI start-up) calls this to wire a
    Rich/REPL-aware printer in place of the stderr default.
    """
    global _printer
    _printer = printer

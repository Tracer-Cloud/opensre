from __future__ import annotations

from surfaces.shared.terminal.components.time_format import _fmt_timing
from surfaces.shared.terminal.output.console_state import (
    set_live_console,
    stop_display,
    unregister_live_console,
)
from surfaces.shared.terminal.output.environment import (
    _repl_progress_active,
    _safe_print,
    debug_print,
    get_output_format,
)
from surfaces.shared.terminal.output.events import ProgressEvent
from surfaces.shared.terminal.output.renderers import (
    render_divider,
    render_event,
    render_footer,
)
from surfaces.shared.terminal.output.toggles import (
    ToolDetailToggleWatcher,
    register_tool_detail_toggle,
    suppress_stdin_watchers,
    toggle_active_tool_details,
)
from surfaces.shared.terminal.output.tracker import (
    ProgressTracker,
    get_tracker,
    reset_tracker,
    set_silent_tracker,
    set_tracker_console,
)

__all__ = [
    # Tracker / progress
    "ProgressEvent",
    "ProgressTracker",
    "get_tracker",
    "reset_tracker",
    "set_tracker_console",
    "set_silent_tracker",
    # Rendering
    "render_divider",
    "render_event",
    "render_footer",
    # Console lifecycle
    "set_live_console",
    "stop_display",
    "unregister_live_console",
    # Tool-detail toggle
    "ToolDetailToggleWatcher",
    "register_tool_detail_toggle",
    "suppress_stdin_watchers",
    "toggle_active_tool_details",
    # Output config
    "debug_print",
    "get_output_format",
    # Package-internal helpers for ui/stream_renderer (underscore names are
    # intentional — they signal "reach in carefully" rather than stable API)
    "_fmt_timing",
    "_repl_progress_active",
    "_safe_print",
]

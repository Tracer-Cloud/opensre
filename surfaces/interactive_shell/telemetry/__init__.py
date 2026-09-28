"""Interactive-shell analytics enrichment."""

from surfaces.interactive_shell.telemetry.console_capture import capture_console_segment
from surfaces.interactive_shell.telemetry.integration_snapshot import (
    build_turn_integration_snapshot,
)
from surfaces.interactive_shell.telemetry.turn_outcome import (
    format_terminal_turn_outcome,
    format_wizard_cli_outcome,
    slash_command_is_interactive_wizard,
    slash_command_is_summary_only,
    truncate_analytics_text,
)

__all__ = [
    "build_turn_integration_snapshot",
    "capture_console_segment",
    "format_terminal_turn_outcome",
    "format_wizard_cli_outcome",
    "slash_command_is_interactive_wizard",
    "slash_command_is_summary_only",
    "truncate_analytics_text",
]

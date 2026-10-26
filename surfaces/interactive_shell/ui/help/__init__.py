"""Interactive help: the slash-command browser and its printed fallbacks."""

from surfaces.interactive_shell.ui.help.help_browser import browse_help_commands
from surfaces.interactive_shell.ui.help.help_menu import (
    HelpSection,
    has_help_details,
    render_command_detail,
    render_help_index,
    render_section_detail,
)

__all__ = [
    "HelpSection",
    "browse_help_commands",
    "has_help_details",
    "render_command_detail",
    "render_help_index",
    "render_section_detail",
]

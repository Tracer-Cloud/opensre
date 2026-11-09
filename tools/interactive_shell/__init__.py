"""Interactive-shell tools.

Import tool submodules explicitly (for example
``tools.interactive_shell.actions.slash``)
rather than relying on this package initializer to eagerly import them.

``contracts`` lives in this package and is imported by
``command_registry.slash_catalog`` during early import wiring. Eagerly importing
the tool submodules here (several of which import back into ``command_registry``)
would reintroduce a circular import during interactive-shell startup.
"""

from __future__ import annotations

TOOL_MODULES = (
    "actions.ask_choice",
    "actions.cli_command",
    "actions.implementation",
    "actions.llm_provider",
    "actions.propose_scheduled_delivery",
    "actions.sentry_fix",
    "actions.session_goal",
    "actions.shell",
    "actions.skill_view",
    "actions.slash",
    "actions.task_cancel",
    "actions.update_plan",
)

__all__ = ["TOOL_MODULES"]

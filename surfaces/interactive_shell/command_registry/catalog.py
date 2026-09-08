"""Merged slash-command catalog for the interactive REPL."""

from __future__ import annotations

from itertools import chain

from surfaces.interactive_shell.command_registry.agents import COMMANDS as AGENTS_COMMANDS
from surfaces.interactive_shell.command_registry.alerts import COMMANDS as ALERTS_COMMANDS
from surfaces.interactive_shell.command_registry.choice_prompt import (
    COMMANDS as CHOICE_COMMANDS,
)
from surfaces.interactive_shell.command_registry.cli_parity import (
    COMMANDS as PARITY_COMMANDS,
)
from surfaces.interactive_shell.command_registry.demo_cmds import COMMANDS as DEMO_COMMANDS
from surfaces.interactive_shell.command_registry.diagnostics_cmds import (
    COMMANDS as DIAGNOSTICS_COMMANDS,
)
from surfaces.interactive_shell.command_registry.gateway_cmds import (
    COMMANDS as GATEWAY_COMMANDS,
)
from surfaces.interactive_shell.command_registry.help import COMMANDS as HELP_COMMANDS
from surfaces.interactive_shell.command_registry.integrations import (
    COMMANDS as INTEGRATIONS_COMMANDS,
)
from surfaces.interactive_shell.command_registry.loops_cmds import COMMANDS as LOOPS_COMMANDS
from surfaces.interactive_shell.command_registry.memory_cmds import (
    COMMANDS as MEMORY_COMMANDS,
)
from surfaces.interactive_shell.command_registry.model import COMMANDS as MODEL_COMMANDS
from surfaces.interactive_shell.command_registry.privacy_cmds import (
    COMMANDS as PRIVACY_COMMANDS,
)
from surfaces.interactive_shell.command_registry.remote_sync_cmds import (
    COMMANDS as REMOTE_SYNC_COMMANDS,
)
from surfaces.interactive_shell.command_registry.session_cmds import (
    COMMANDS as SESSION_COMMANDS,
)
from surfaces.interactive_shell.command_registry.settings_cmds import (
    COMMANDS as SETTINGS_COMMANDS,
)
from surfaces.interactive_shell.command_registry.system import COMMANDS as SYSTEM_COMMANDS
from surfaces.interactive_shell.command_registry.tasks_cmds import COMMANDS as TASK_COMMANDS
from surfaces.interactive_shell.command_registry.theme import COMMANDS as THEME_COMMANDS
from surfaces.interactive_shell.command_registry.tools_cmds import COMMANDS as TOOLS_COMMANDS
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.command_registry.work_cmds import COMMANDS as WORK_COMMANDS

_MERGED_SEQUENCE = tuple(
    chain(
        HELP_COMMANDS,
        SESSION_COMMANDS,
        THEME_COMMANDS,
        CHOICE_COMMANDS,
        SETTINGS_COMMANDS,
        DIAGNOSTICS_COMMANDS,
        INTEGRATIONS_COMMANDS,
        MODEL_COMMANDS,
        TOOLS_COMMANDS,
        LOOPS_COMMANDS,
        DEMO_COMMANDS,
        TASK_COMMANDS,
        GATEWAY_COMMANDS,
        PRIVACY_COMMANDS,
        MEMORY_COMMANDS,
        WORK_COMMANDS,
        REMOTE_SYNC_COMMANDS,
        AGENTS_COMMANDS,
        ALERTS_COMMANDS,
        PARITY_COMMANDS,
        SYSTEM_COMMANDS,
    )
)

SLASH_COMMANDS: dict[str, SlashCommand] = {cmd.name: cmd for cmd in _MERGED_SEQUENCE}

__all__ = ["SLASH_COMMANDS"]

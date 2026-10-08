"""Slash commands: /help and /?."""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.markup import escape

from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import ERROR
from surfaces.interactive_shell.ui.help import (
    HelpSection,
    browse_help_commands,
    render_command_detail,
    render_help_index,
    render_section_detail,
)
from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive
from surfaces.shared.terminal.components.subcommand_menu import repl_choose_subcommand

QUICK_ACCESS_COMMANDS: list[str] = [
    "/integrations",
    "/model",
    "/health",
    "/status",
    "/help",
]


def _quick_access_section() -> HelpSection:
    from surfaces.interactive_shell.command_registry import SLASH_COMMANDS

    cmds = [SLASH_COMMANDS[n] for n in QUICK_ACCESS_COMMANDS if n in SLASH_COMMANDS]
    return ("Quick Access", cmds)


def _raw_help_sections() -> list[HelpSection]:
    from surfaces.interactive_shell.command_registry.agents import COMMANDS as AGENTS_CMDS
    from surfaces.interactive_shell.command_registry.alerts import COMMANDS as ALERTS_CMDS
    from surfaces.interactive_shell.command_registry.choice_prompt import (
        COMMANDS as CHOICE_CMDS,
    )
    from surfaces.interactive_shell.command_registry.cli_parity import (
        COMMANDS as PARITY_COMMANDS,
    )
    from surfaces.interactive_shell.command_registry.demo_cmds import COMMANDS as DEMO_CMDS
    from surfaces.interactive_shell.command_registry.diagnostics_cmds import (
        COMMANDS as DIAGNOSTICS_CMDS,
    )
    from surfaces.interactive_shell.command_registry.gateway_cmds import (
        COMMANDS as GATEWAY_CMDS,
    )
    from surfaces.interactive_shell.command_registry.integrations import COMMANDS as INT_CMDS
    from surfaces.interactive_shell.command_registry.loops_cmds import COMMANDS as LOOPS_CMDS
    from surfaces.interactive_shell.command_registry.memory_cmds import (
        COMMANDS as MEMORY_CMDS,
    )
    from surfaces.interactive_shell.command_registry.model import COMMANDS as MODEL_CMDS
    from surfaces.interactive_shell.command_registry.privacy_cmds import (
        COMMANDS as PRIVACY_CMDS,
    )
    from surfaces.interactive_shell.command_registry.remote_sync_cmds import (
        COMMANDS as REMOTE_SYNC_CMDS,
    )
    from surfaces.interactive_shell.command_registry.session_cmds import (
        COMMANDS as SESSION_CMDS,
    )
    from surfaces.interactive_shell.command_registry.settings_cmds import (
        COMMANDS as SETTINGS_CMDS,
    )
    from surfaces.interactive_shell.command_registry.system import COMMANDS as SYS_CMDS
    from surfaces.interactive_shell.command_registry.tasks_cmds import COMMANDS as TASK_CMDS
    from surfaces.interactive_shell.command_registry.theme import COMMANDS as THEME_CMDS
    from surfaces.interactive_shell.command_registry.tools_cmds import COMMANDS as TOOLS_CMDS
    from surfaces.interactive_shell.command_registry.work_cmds import COMMANDS as WORK_CMDS

    return [
        _quick_access_section(),
        ("Help", list(COMMANDS)),
        (
            "Session",
            list(SESSION_CMDS)
            + list(CHOICE_CMDS)
            + list(DEMO_CMDS)
            + list(SETTINGS_CMDS)
            + list(DIAGNOSTICS_CMDS),
        ),
        ("Integrations, Models & Tools", list(INT_CMDS) + list(MODEL_CMDS) + list(TOOLS_CMDS)),
        ("Privacy", list(PRIVACY_CMDS) + list(MEMORY_CMDS)),
        ("Remote sync", list(REMOTE_SYNC_CMDS)),
        (
            "Tasks",
            list(WORK_CMDS) + list(LOOPS_CMDS) + list(TASK_CMDS) + list(GATEWAY_CMDS),
        ),
        ("Theme", list(THEME_CMDS)),
        ("Agents", list(AGENTS_CMDS)),
        ("Alerts", list(ALERTS_CMDS)),
        ("CLI (parity)", list(PARITY_COMMANDS)),
        ("System", list(SYS_CMDS)),
    ]


_QUICK_ACCESS_SECTION_NAME = "Quick Access"


def _help_sections() -> list[HelpSection]:
    """Return user-visible help sections with duplicate command names hidden.

    The "Quick Access" section is intentionally exempted from the dedup set so
    its curated commands remain visible in their canonical sections too (e.g.
    ``/help session`` still contains ``/status``).
    """
    seen: set[str] = set()
    sections: list[HelpSection] = []
    for section_name, commands in _raw_help_sections():
        visible: list[SlashCommand] = []
        is_quick_access = section_name == _QUICK_ACCESS_SECTION_NAME
        for command in commands:
            if command.name in seen:
                continue
            visible.append(command)
            if not is_quick_access:
                seen.add(command.name)
        sections.append((section_name, visible))
    return sections


def _find_command(sections: Sequence[HelpSection], target: str) -> SlashCommand | None:
    normalized = target.strip().lower()
    if not normalized:
        return None
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    for _section_name, commands in sections:
        for command in commands:
            if command.name.lower() == normalized:
                return command
    return None


def _find_section(
    sections: Sequence[HelpSection],
    target: str,
) -> tuple[str, Sequence[SlashCommand]] | None:
    normalized = target.strip().lower().replace("-", " ")
    for section_name, commands in sections:
        aliases = {
            section_name.lower(),
            section_name.lower().replace("&", "and"),
            section_name.lower().replace(" & ", " "),
        }
        if normalized in aliases:
            return section_name, commands
    return None


def _subcommand_options(command: SlashCommand) -> tuple[tuple[str, str], ...]:
    """Named subcommands of ``command``, excluding ``--flag`` style completions."""
    return tuple(
        (name, meta) for name, meta in command.first_arg_completions if not name.startswith("-")
    )


def _command_text(command: SlashCommand) -> str | None:
    """Text to dispatch for a help selection, or ``None`` when the user backs out.

    A command with subcommands opens its picker first: selecting it in the
    browser should offer the same choice the composer tray offers while typing
    it, not silently run whichever subcommand happens to be the default.
    """
    options = _subcommand_options(command)
    if not options:
        return command.name
    chosen = repl_choose_subcommand(parent=command.name, options=options)
    return None if chosen is None else f"{command.name} {chosen}"


def _cmd_help(_session: Session, console: Console, args: list[str]) -> bool:
    sections = _help_sections()
    if args:
        target = " ".join(args).strip()
        if target.lower() in {"all", "commands"}:
            render_help_index(console, sections)
            return True
        if not target.startswith("/"):
            section = _find_section(sections, target)
            if section is not None:
                section_name, commands = section
                render_section_detail(console, section_name, commands)
                return True
        command = _find_command(sections, target)
        if command is not None:
            render_command_detail(console, command)
            return True
        console.print(f"[{ERROR}]unknown help topic:[/] {escape(target)}")
        console.print(
            "Try [bold]/help[/bold], [bold]/help /model[/bold], or [bold]/help tasks[/bold]."
        )
        return True

    if repl_tty_interactive():
        selected = browse_help_commands(sections)
        if not selected:
            return True
        command = _find_command(sections, selected)
        text = selected if command is None else _command_text(command)
        if text is None:
            return True
        # Run the selection rather than only describing it, so Enter in the
        # help browser is the same gesture as typing the command.
        from surfaces.interactive_shell.command_registry import dispatch_slash

        return dispatch_slash(text, _session, console)

    render_help_index(console, sections)
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/help",
        "Show available commands.",
        _cmd_help,
        usage=("/help", "/help <command>", "/help <category>"),
        examples=("/help /model", "/help tasks"),
    ),
    SlashCommand("/?", "Shortcut for /help.", _cmd_help),
]

__all__ = ["COMMANDS", "QUICK_ACCESS_COMMANDS"]

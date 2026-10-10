"""REPL adapter for slash-command action tools."""

from __future__ import annotations

import functools
import shlex
from collections.abc import Callable
from typing import Any, Protocol

from rich.console import Console

from surfaces.interactive_shell.command_registry import SLASH_COMMANDS, dispatch_slash
from surfaces.interactive_shell.command_registry.slash_catalog import (
    slash_invoke_input_schema,
    slash_invoke_tool_description,
)
from surfaces.interactive_shell.runtime.exclusive_output import with_exclusive_output
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.telemetry.turn_outcome import (
    format_terminal_turn_outcome,
)
from surfaces.interactive_shell.ui import repl_tty_interactive
from surfaces.interactive_shell.ui.execution_confirm import execution_allowed
from tools.interactive_shell.shared.execution_policy import ExecutionPolicyResult
from tools.interactive_shell.shared.host_contracts import ExecutionGate


class SlashPorts(ExecutionGate, Protocol):
    def command_exists(self, name: str) -> bool:
        raise NotImplementedError

    def command_is_mutating(self, name: str) -> bool:
        """Whether running command ``name`` can change state (control commands cannot)."""

    def tool_description(self) -> str:
        raise NotImplementedError

    def input_schema(self) -> dict[str, Any]:
        raise NotImplementedError

    def tty_interactive(self) -> bool:
        raise NotImplementedError

    def format_turn_outcome(
        self,
        command: str,
        *,
        ok: bool,
    ) -> str:
        raise NotImplementedError

    def dispatch(
        self,
        command: str,
        *,
        session: Session,
        console: Console,
        confirm_fn: Callable[[str], str] | None,
        is_tty: bool | None,
        policy_precleared: bool = False,
    ) -> bool:
        raise NotImplementedError


class ReplSlashPorts:
    def command_exists(self, name: str) -> bool:
        return name in SLASH_COMMANDS

    def command_is_mutating(self, name: str) -> bool:
        command = SLASH_COMMANDS.get(name)
        return command.mutating if command is not None else True

    def tool_description(self) -> str:
        return slash_invoke_tool_description()

    def input_schema(self) -> dict[str, Any]:
        return slash_invoke_input_schema()

    def tty_interactive(self) -> bool:
        return repl_tty_interactive()

    def format_turn_outcome(
        self,
        command: str,
        *,
        ok: bool,
    ) -> str:
        return format_terminal_turn_outcome(
            command,
            kind="slash",
            ok=ok,
        )

    def execution_allowed(
        self,
        *,
        policy: ExecutionPolicyResult,
        session: Session,
        console: Console,
        action_summary: str,
        confirm_fn: Callable[[str], str] | None,
        is_tty: bool | None,
        action_already_listed: bool,
    ) -> bool:
        return execution_allowed(
            policy,
            session=session,
            console=console,
            action_summary=action_summary,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            action_already_listed=action_already_listed,
        )

    def dispatch(
        self,
        command: str,
        *,
        session: Session,
        console: Console,
        confirm_fn: Callable[[str], str] | None,
        is_tty: bool | None,
        policy_precleared: bool = False,
    ) -> bool:
        dispatch = functools.partial(
            dispatch_slash,
            command,
            session,
            console,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            policy_precleared=policy_precleared,
        )
        # A list observation can feed the next action in the same agent turn.
        # Suspend the prompt for rendering instead of ending that turn with a
        # queued command that loses the remaining work (e.g. remove by task ID).
        try:
            is_cron_list = shlex.split(command)[:2] == ["/cron", "list"]
        except ValueError:
            is_cron_list = False
        if is_tty is not False and self.tty_interactive() and is_cron_list:
            return with_exclusive_output(session, dispatch)
        return dispatch()


class HeadlessSlashPorts(ReplSlashPorts):
    """Slash ports for non-interactive headless and gateway turns."""

    def tty_interactive(self) -> bool:
        return False

    def format_turn_outcome(
        self,
        command: str,
        *,
        ok: bool,
    ) -> str:
        status = "succeeded" if ok else "failed"
        return f"Slash command {command} {status}."


def repl_slash_ports() -> SlashPorts:
    return ReplSlashPorts()


def headless_slash_ports() -> SlashPorts:
    return HeadlessSlashPorts()


__all__ = [
    "HeadlessSlashPorts",
    "ReplSlashPorts",
    "SlashPorts",
    "headless_slash_ports",
    "repl_slash_ports",
]

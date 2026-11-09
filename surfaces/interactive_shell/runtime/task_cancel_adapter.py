"""REPL adapter for task cancellation action tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from rich.console import Console

from surfaces.interactive_shell.command_registry import dispatch_slash
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.execution_confirm import execution_allowed
from tools.interactive_shell.shared.execution_policy import ExecutionPolicyResult
from tools.interactive_shell.shared.host_contracts import ExecutionGate


class TaskCancelPorts(ExecutionGate, Protocol):
    def dispatch_cancel(
        self,
        command: str,
        *,
        session: Session,
        console: Console,
        confirm_fn: Callable[[str], str] | None,
        is_tty: bool | None,
    ) -> bool:
        raise NotImplementedError


class ReplTaskCancelPorts:
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

    def dispatch_cancel(
        self,
        command: str,
        *,
        session: Session,
        console: Console,
        confirm_fn: Callable[[str], str] | None,
        is_tty: bool | None,
    ) -> bool:
        return dispatch_slash(
            command,
            session,
            console,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            policy_precleared=True,
        )


def repl_task_cancel_ports() -> TaskCancelPorts:
    return ReplTaskCancelPorts()


__all__ = [
    "ReplTaskCancelPorts",
    "TaskCancelPorts",
    "repl_task_cancel_ports",
]

"""The approval gate every host capability shares.

An action tool asks its host whether an action may run before running it. The
four host capabilities — named commands, LLM-provider switching, task
cancellation — all need that one step, and each had
declared the same seven-parameter signature itself. It is declared here once,
beside the :class:`ExecutionPolicyResult` it takes.

``session`` stays ``Any``: the concrete type differs per surface and this
contract never reads it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from rich.console import Console

from tools.interactive_shell.shared.execution_policy import ExecutionPolicyResult


@runtime_checkable
class ExecutionGate(Protocol):
    """The host's half of the execution gate: confirm, then allow or refuse."""

    def execution_allowed(
        self,
        *,
        policy: ExecutionPolicyResult,
        session: Any,
        console: Console,
        action_summary: str,
        confirm_fn: Callable[[str], str] | None,
        is_tty: bool | None,
        action_already_listed: bool,
    ) -> bool:
        """Return whether the action may run; the host renders any prompt."""


__all__ = ["ExecutionGate"]

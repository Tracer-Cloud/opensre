"""REPL adapter for LLM provider and reasoning-model switching."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from rich.console import Console

from config.llm_auth.provider_catalog import PROVIDER_BY_VALUE
from surfaces.interactive_shell.command_registry import (
    switch_llm_provider,
    switch_reasoning_model,
)
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.execution_confirm import execution_allowed
from tools.interactive_shell.shared.execution_policy import ExecutionPolicyResult
from tools.interactive_shell.shared.host_contracts import ExecutionGate


class LlmProviderPorts(ExecutionGate, Protocol):
    def apply_target(self, target: str, console: Console) -> bool:
        raise NotImplementedError


class ReplLlmProviderPorts:
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

    def apply_target(self, target: str, console: Console) -> bool:
        candidate = target.strip()
        if candidate.lower() in PROVIDER_BY_VALUE:
            return switch_llm_provider(candidate, console)
        return switch_reasoning_model(candidate, console)


def repl_llm_provider_ports() -> LlmProviderPorts:
    return ReplLlmProviderPorts()


__all__ = [
    "LlmProviderPorts",
    "ReplLlmProviderPorts",
    "repl_llm_provider_ports",
]

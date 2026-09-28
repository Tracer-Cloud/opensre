"""Shared contracts reused across interactive-shell tools.

This package is the single import path for the cross-tool execution policy:
``from tools.interactive_shell.shared import ...``. Tool modules should import
the policy contracts and helpers from here rather than from the underlying
``execution_policy`` module.
"""

from __future__ import annotations

from tools.interactive_shell.shared.execution_policy import (
    ConfirmationOutcome,
    ConfirmationPlan,
    ExecutionPolicyResult,
    ExecutionVerdict,
    ToolExecutionMode,
    ToolExecutionPlan,
    allow_tool,
    apply_auto_level,
    apply_plan_only_gate,
    ask_tool,
    is_mutating_tool_type,
    plan_foreground_tool,
    resolve_confirmation,
)

__all__ = [
    "ConfirmationOutcome",
    "ConfirmationPlan",
    "ExecutionPolicyResult",
    "ExecutionVerdict",
    "ToolExecutionMode",
    "ToolExecutionPlan",
    "allow_tool",
    "apply_auto_level",
    "apply_plan_only_gate",
    "ask_tool",
    "is_mutating_tool_type",
    "plan_foreground_tool",
    "resolve_confirmation",
]

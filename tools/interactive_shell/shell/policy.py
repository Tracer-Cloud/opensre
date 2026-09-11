"""Shell-specific execution policy for the interactive REPL.

Alpha mode allows every shell command; the only rejected case is genuinely
empty input. These helpers live next to the rest of the shell machinery so the shared
execution-policy module is not imported for shell-only concerns by other tools.
They reuse the shared policy contracts
(``ExecutionPolicyResult`` / ``ToolExecutionPlan``) from
``tools.interactive_shell.shared``.
"""

from __future__ import annotations

from tools.interactive_shell.shared import (
    ExecutionPolicyResult,
    ToolExecutionMode,
    ToolExecutionPlan,
)
from tools.interactive_shell.shell.parsing import (
    ParsedShellCommand,
    parse_shell_command,
)
from tools.interactive_shell.shell.read_only import is_read_only_shell_command


def evaluate_shell_from_parsed(parsed: ParsedShellCommand) -> ExecutionPolicyResult:
    """Alpha mode: allow every shell command; only reject empty input.

    There is no command classification or deny floor — any command (mutating,
    ``restricted``, operators, substitution, passthrough) is allowed. A
    ``parse_error`` only occurs for empty input (e.g. a bare ``!``), which is
    rejected because there is nothing to run.
    """
    if parsed.parse_error is not None:
        return ExecutionPolicyResult(
            verdict="deny",
            tool_type="shell",
            reason=parsed.parse_error,
            hint="Enter a command to run.",
            shell_classification="unrestricted",
        )

    classification = "read_only" if is_read_only_shell_command(parsed.command) else "unrestricted"
    return ExecutionPolicyResult(
        verdict="allow",
        tool_type="shell",
        reason=None,
        shell_classification=classification,
    )


def plan_shell_execution(parsed: ParsedShellCommand) -> ToolExecutionPlan:
    policy = evaluate_shell_from_parsed(parsed)
    classification = policy.shell_classification or "unrestricted"
    return ToolExecutionPlan(
        tool_type="shell",
        classification=classification,
        execution_mode=ToolExecutionMode.FOREGROUND,
        policy=policy,
    )


def evaluate_shell_command(command: str) -> ExecutionPolicyResult:
    """Map shell policy + passthrough rules into allow/ask/deny."""
    parsed = parse_shell_command(command)
    return evaluate_shell_from_parsed(parsed)


__all__ = [
    "evaluate_shell_command",
    "evaluate_shell_from_parsed",
    "plan_shell_execution",
]

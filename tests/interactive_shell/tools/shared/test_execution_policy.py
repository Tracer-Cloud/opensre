"""Unit tests for the shared REPL execution policy.

Alpha mode: policy helpers resolve to ``allow`` with no confirmation prompt and
there is no command guardrail. The ``ask`` verdict is retained for
``trust_mode`` / future opt-in stricter policy, so those paths are covered here
by exercising :func:`resolve_confirmation` with explicitly-constructed ``ask`` /
``deny`` results.

This module is pure (no console, no ``input``, no analytics). The interaction
layer (``execution_allowed``) and its terminal/analytics behavior are covered by
``tests/interactive_shell/ui/test_execution_confirm.py``. Shell-specific policy
lives in ``tools.interactive_shell.shell.policy`` and is covered by
``tests/interactive_shell/shell/test_policy.py``.
"""

from __future__ import annotations

from config.constants.repl_autonomy import AutoLevel
from tools.interactive_shell.shared import (
    ConfirmationOutcome,
    ExecutionPolicyResult,
    ToolExecutionMode,
    ToolExecutionPlan,
    allow_tool,
    apply_auto_level,
    apply_plan_only_gate,
    plan_foreground_tool,
    resolve_confirmation,
)


def _ask_result() -> ExecutionPolicyResult:
    """An explicit ``ask`` verdict (the default policy no longer emits these)."""
    return ExecutionPolicyResult(
        verdict="ask",
        tool_type="slash",
        reason="this command may change configuration or run heavy work",
    )


# --- Default-allow policy decisions -----------------------------------------


def test_allow_tool_is_allow() -> None:
    r = allow_tool("slash")
    assert r.verdict == "allow"
    assert r.tool_type == "slash"
    assert r.reason is None


def test_allow_tool_carries_arbitrary_tool_type() -> None:
    for tool_type in ("cli_command", "sentry_issue_fix", "switch_llm_provider", "code_agent"):
        r = allow_tool(tool_type)
        assert r.verdict == "allow"
        assert r.tool_type == tool_type


# --- plan_foreground_tool ---------------------------------------------------


def test_plan_foreground_tool_defaults_classification_to_tool_type() -> None:
    plan = plan_foreground_tool("slash")
    assert isinstance(plan, ToolExecutionPlan)
    assert plan.tool_type == "slash"
    assert plan.classification == "slash"
    assert plan.execution_mode is ToolExecutionMode.FOREGROUND
    assert plan.policy.verdict == "allow"


def test_plan_foreground_tool_accepts_explicit_classification() -> None:
    plan = plan_foreground_tool("cli_command", "cli_launch")
    assert plan.tool_type == "cli_command"
    assert plan.classification == "cli_launch"
    assert plan.execution_mode is ToolExecutionMode.FOREGROUND
    assert plan.policy.verdict == "allow"


# --- resolve_confirmation: pure decision (no side effects) ------------------


def test_resolve_allow_verdict_proceeds() -> None:
    plan = resolve_confirmation(allow_tool("slash"), trust_mode=False, is_tty=True)
    assert plan.outcome == ConfirmationOutcome.ALLOW
    assert plan.analytics_outcome == "allowed"


def test_resolve_deny_verdict_blocks() -> None:
    result = ExecutionPolicyResult(
        verdict="deny",
        tool_type="shell",
        reason="empty command.",
        hint="Enter a command to run.",
    )
    plan = resolve_confirmation(result, trust_mode=False, is_tty=True)
    assert plan.outcome == ConfirmationOutcome.DENY
    assert plan.analytics_outcome == "blocked"
    assert plan.analytics_reason == "empty command."


def test_resolve_ask_trust_mode_allows_without_prompt() -> None:
    plan = resolve_confirmation(_ask_result(), trust_mode=True, is_tty=True)
    assert plan.outcome == ConfirmationOutcome.ALLOW
    assert plan.analytics_outcome == "allowed"
    assert plan.analytics_reason == "trust_mode_skipped_prompt"


def test_resolve_ask_non_tty_blocks() -> None:
    plan = resolve_confirmation(_ask_result(), trust_mode=False, is_tty=False)
    assert plan.outcome == ConfirmationOutcome.BLOCK_NON_TTY
    assert plan.analytics_outcome == "blocked"
    assert plan.analytics_reason == "non_interactive_stdin"


def test_resolve_ask_tty_needs_confirmation() -> None:
    plan = resolve_confirmation(_ask_result(), trust_mode=False, is_tty=True)
    assert plan.outcome == ConfirmationOutcome.NEEDS_CONFIRMATION
    # The analytics outcome for a prompt is decided by the interaction layer.
    assert plan.analytics_outcome is None
    assert plan.analytics_reason is None


def test_auto_high_leaves_default_allow() -> None:
    result = apply_auto_level(allow_tool("shell"), AutoLevel.HIGH)
    assert result.verdict == "allow"


def test_auto_med_asks_mutating_tools_and_allows_read_only() -> None:
    # Med "allow reversible commands": mutation-capable tools still require
    # approval (including agent-selected slash/CLI and synthetic_test); read-only
    # investigation runs without a prompt.
    for mutating in (
        "shell",
        "code_agent",
        "slash",
        "cli_command",
        "opensre_cli",
        "switch_llm_provider",
        "synthetic_test",
        "sentry_issue_fix",
    ):
        result = apply_auto_level(allow_tool(mutating), AutoLevel.MED)
        assert result.verdict == "ask", mutating
        assert "Auto (Med)" in (result.reason or "")
    read_only = apply_auto_level(allow_tool("investigation"), AutoLevel.MED)
    assert read_only.verdict == "allow"


def test_auto_low_asks_mutating_tool_types() -> None:
    for tool_type in ("slash", "shell", "code_agent", "cli_command"):
        result = apply_auto_level(allow_tool(tool_type), AutoLevel.LOW)
        assert result.verdict == "ask", tool_type


def test_auto_med_allows_sample_alert() -> None:
    # Sample alerts are investigation-shaped; Med still allows them (Low gates).
    assert apply_auto_level(allow_tool("sample_alert"), AutoLevel.MED).verdict == "allow"


def test_auto_off_asks_every_tool_type() -> None:
    result = apply_auto_level(allow_tool("slash"), AutoLevel.OFF)
    assert result.verdict == "ask"


# --- plan-only execution gate (pure decision) --------------------------------


def test_plan_only_gate_asks_before_every_mutating_tool_even_at_high() -> None:
    # A standing plan-only request gates mutating tools regardless of /auto.
    for mutating in ("shell", "code_agent", "slash", "synthetic_test", "sentry_issue_fix"):
        result = apply_plan_only_gate(allow_tool(mutating), plan_only_active=True)
        assert result.verdict == "ask", mutating
    # Read-only tool types still run — plan-only gates mutation, not evidence.
    read_only = apply_plan_only_gate(allow_tool("investigation"), plan_only_active=True)
    assert read_only.verdict == "allow"


def test_plan_only_gate_is_a_noop_when_not_active() -> None:
    assert apply_plan_only_gate(allow_tool("shell"), plan_only_active=False).verdict == "allow"


def test_plan_only_gate_leaves_a_non_allow_verdict_untouched() -> None:
    denied = ExecutionPolicyResult(verdict="deny", tool_type="shell", reason="empty")
    assert apply_plan_only_gate(denied, plan_only_active=True).verdict == "deny"

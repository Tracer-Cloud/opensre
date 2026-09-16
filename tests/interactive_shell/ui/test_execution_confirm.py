"""Tests for the execution-policy interaction layer (``execution_allowed``).

These cover the terminal-facing half of the execution gate: console output and
the confirmation prompt. The pure decision is tested in
``tests/tools/interactive_shell/shared/test_execution_policy.py``.
"""

from __future__ import annotations

import io

from rich.console import Console

from config.constants.repl_autonomy import AutoLevel
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.execution_confirm import execution_allowed
from tools.interactive_shell.shared import (
    ExecutionPolicyResult,
    allow_tool,
)


def _ask_result() -> ExecutionPolicyResult:
    """An explicit ``ask`` verdict (the default policy no longer emits these)."""
    return ExecutionPolicyResult(
        verdict="ask",
        tool_type="slash",
        reason="this command may change configuration or run heavy work",
    )


# --- execution_allowed: default-allow runs without prompting ----------------


def test_allow_verdict_runs_without_prompt() -> None:
    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    def _confirm(_: str) -> str:  # pragma: no cover - must never be called
        raise AssertionError("default-allow must not prompt for confirmation")

    r = allow_tool("slash")
    assert execution_allowed(
        r,
        session=session,
        console=console,
        action_summary="/integrations verify foo",
        confirm_fn=_confirm,
        is_tty=True,
    )
    assert "Confirm" not in buf.getvalue()


def test_non_tty_allows_default_policy() -> None:
    """Default-allow no longer fails closed on non-interactive stdin."""
    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    r = allow_tool("slash")
    assert execution_allowed(
        r,
        session=session,
        console=console,
        action_summary="/save out.md",
        is_tty=False,
    )


def test_deny_verdict_blocks() -> None:
    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    # The default policy never emits a deny; construct one explicitly to cover
    # the execution_allowed deny path.
    r = ExecutionPolicyResult(
        verdict="deny",
        tool_type="shell",
        reason="empty command.",
        hint="Enter a command to run.",
    )
    assert not execution_allowed(
        r,
        session=session,
        console=console,
        action_summary="!",
        is_tty=True,
    )
    assert "blocked" in buf.getvalue()


# --- Retained ask machinery (reachable only via explicit ask) ---------------


def test_explicit_ask_trust_mode_allows() -> None:
    session = Session()
    session.terminal.trust_mode = True
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    assert execution_allowed(
        _ask_result(),
        session=session,
        console=console,
        action_summary="/investigate x",
        confirm_fn=lambda _: "n",
        is_tty=True,
    )


def test_explicit_ask_non_tty_blocks() -> None:
    session = Session()
    session.terminal.trust_mode = False
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    assert not execution_allowed(
        _ask_result(),
        session=session,
        console=console,
        action_summary="/save out.md",
        is_tty=False,
    )
    assert "not a TTY" in buf.getvalue()


def test_explicit_ask_tty_accepts_empty_confirmation() -> None:
    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    captured: list[str] = []

    def _confirm(prompt: str) -> str:
        captured.append(prompt)
        return ""

    assert execution_allowed(
        _ask_result(),
        session=session,
        console=console,
        action_summary="/integrations verify foo",
        confirm_fn=_confirm,
        is_tty=True,
    )
    assert captured == ["Approve this action?"]
    assert "Command to approve" in buf.getvalue()
    assert "Why this needs approval:" in buf.getvalue()


def test_explicit_ask_tty_rejects_explicit_no() -> None:
    session = Session()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    assert not execution_allowed(
        _ask_result(),
        session=session,
        console=console,
        action_summary="/integrations verify foo",
        confirm_fn=lambda _: "n",
        is_tty=True,
    )
    assert "cancelled" in buf.getvalue()


def test_auto_med_prompts_before_mutating_shell() -> None:
    session = Session()
    session.terminal.auto_level = AutoLevel.MED
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    prompted = {"asked": False}

    def _confirm(_: str) -> str:
        prompted["asked"] = True
        return "n"

    # Med must not silently run a mutation-capable shell command.
    assert not execution_allowed(
        allow_tool("shell"),
        session=session,
        console=console,
        action_summary="!pytest",
        confirm_fn=_confirm,
        is_tty=True,
    )
    assert prompted["asked"] is True
    assert "Command to approve" in buf.getvalue()


def test_auto_med_prompts_before_agent_slash_mutation() -> None:
    """Med must ask before agent-selected mutating slash (e.g. integrations remove)."""
    session = Session()
    session.terminal.auto_level = AutoLevel.MED
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    prompted = {"asked": False}

    def _confirm(_: str) -> str:
        prompted["asked"] = True
        return "n"

    assert not execution_allowed(
        allow_tool("slash"),
        session=session,
        console=console,
        action_summary="/integrations remove posthog",
        confirm_fn=_confirm,
        is_tty=True,
    )
    assert prompted["asked"] is True


def test_auto_med_prompts_before_sentry_issue_fix() -> None:
    """Med must ask before a Sentry issue-fix that can edit, commit, and open a PR."""
    session = Session()
    session.terminal.auto_level = AutoLevel.MED
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    prompted = {"asked": False}

    def _confirm(_: str) -> str:
        prompted["asked"] = True
        return "n"

    assert not execution_allowed(
        allow_tool("sentry_issue_fix"),
        session=session,
        console=console,
        action_summary="fix Sentry issue https://acme.sentry.io/issues/1/ and open a pull request",
        confirm_fn=_confirm,
        is_tty=True,
    )
    assert prompted["asked"] is True
    assert "Command to approve" in buf.getvalue()


def test_auto_med_shows_the_merge_push_action_even_when_the_plan_was_listed() -> None:
    """The merge push never appears in the numbered plan, so the card must name it."""
    # Arrange
    session = Session()
    session.terminal.auto_level = AutoLevel.MED
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    action = "commit the merge of main into feature and push it to origin/feature"

    # Act
    allowed = execution_allowed(
        allow_tool("merge_push"),
        session=session,
        console=console,
        action_summary=action,
        confirm_fn=lambda _prompt: "n",
        is_tty=True,
        action_already_listed=True,
    )

    # Assert
    assert allowed is False
    assert action in buf.getvalue()


def test_auto_off_shows_command_to_approve() -> None:
    session = Session()
    session.terminal.auto_level = AutoLevel.OFF
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    assert execution_allowed(
        allow_tool("slash"),
        session=session,
        console=console,
        action_summary="/status",
        confirm_fn=lambda _: "y",
        is_tty=True,
    )
    assert "Command to approve" in buf.getvalue()
    assert "Why this needs approval:" in buf.getvalue()


# --- plan-only execution gate (integration + latch clearing) -----------------


def test_plan_only_guard_preserves_latch_after_approving_one_shell_command() -> None:
    session = Session()
    session.plan_only_until_authorized = True
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    # Approving one opaque shell command does not authorize the rest of a plan.
    assert execution_allowed(
        allow_tool("shell"),
        session=session,
        console=console,
        action_summary="ls",
        confirm_fn=lambda _: "y",
        is_tty=True,
    )
    assert "Command to approve" in buf.getvalue()
    assert session.plan_only_until_authorized is True
    assert session.terminal.pending_confirm_options[1] == ("allow_plan", "Yes, run the plan")


def test_plan_only_shell_can_explicitly_authorize_the_plan() -> None:
    session = Session()
    session.plan_only_until_authorized = True
    console = Console(file=io.StringIO(), force_terminal=False)

    assert execution_allowed(
        allow_tool("shell"),
        session=session,
        console=console,
        action_summary="ls",
        confirm_fn=lambda _: "allow_plan",
        is_tty=True,
    )
    assert session.plan_only_until_authorized is False


def test_plan_only_guard_clears_on_confirmed_non_shell_mutation() -> None:
    session = Session()
    session.plan_only_until_authorized = True
    console = Console(file=io.StringIO(), force_terminal=False)

    assert execution_allowed(
        allow_tool("slash"),
        session=session,
        console=console,
        action_summary="/save out.md",
        confirm_fn=lambda _: "y",
        is_tty=True,
    )
    assert session.plan_only_until_authorized is False


def test_plan_only_guard_survives_a_declined_confirmation() -> None:
    session = Session()
    session.plan_only_until_authorized = True
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    assert not execution_allowed(
        allow_tool("shell"),
        session=session,
        console=console,
        action_summary="!deploy",
        confirm_fn=lambda _: "n",
        is_tty=True,
    )
    # Declining does not authorize; the guard still stands for the next step.
    assert session.plan_only_until_authorized is True


def test_plan_only_guard_is_not_cleared_by_a_read_only_step() -> None:
    session = Session()
    session.plan_only_until_authorized = True
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    # A read-only tool runs without a prompt and must NOT lift the plan-only latch.
    assert execution_allowed(
        allow_tool("investigation"),
        session=session,
        console=console,
        action_summary="investigate the 502s",
        is_tty=True,
    )
    assert session.plan_only_until_authorized is True


def test_shell_approval_does_not_offer_auto_escalation() -> None:
    session = Session()
    session.terminal.auto_level = AutoLevel.LOW
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    assert execution_allowed(
        ExecutionPolicyResult(verdict="ask", tool_type="shell", reason=None),
        session=session,
        console=console,
        action_summary="echo hi > /tmp/s1.txt",
        confirm_fn=lambda _: "y",
        is_tty=True,
    )
    assert session.terminal.auto_level is AutoLevel.LOW
    assert session.terminal.pending_confirm_options == (("y", "Yes, allow"), ("n", "No, cancel"))
    assert "medium risk" in buf.getvalue()


def test_plain_yes_does_not_change_the_auto_level() -> None:
    from config.constants.repl_autonomy import AutoLevel

    session = Session()
    session.terminal.auto_level = AutoLevel.LOW
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    assert execution_allowed(
        ExecutionPolicyResult(verdict="ask", tool_type="shell", reason=None),
        session=session,
        console=console,
        action_summary="echo hi > /tmp/s1.txt",
        confirm_fn=lambda _: "y",
        is_tty=True,
    )
    assert session.terminal.auto_level is AutoLevel.LOW


def test_non_shell_action_gets_plain_confirmation_no_risk_grade() -> None:
    from config.constants.repl_autonomy import AutoLevel

    session = Session()
    session.terminal.auto_level = AutoLevel.LOW
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    captured: list[str] = []

    # The unoffered "always" answer cannot silently approve or raise autonomy.
    assert not execution_allowed(
        ExecutionPolicyResult(verdict="ask", tool_type="slash", reason="explains itself"),
        session=session,
        console=console,
        action_summary="/auto low",
        confirm_fn=lambda p: captured.append(p) or "always",
        is_tty=True,
    )
    out = buf.getvalue()
    assert "needs confirmation" in out
    assert "low risk" not in out
    assert "medium risk" not in out
    assert "high risk" not in out
    assert "always allow" not in out
    assert session.terminal.auto_level is AutoLevel.LOW

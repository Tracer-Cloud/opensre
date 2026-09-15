"""Tests for shell-specific execution policy.

Alpha mode allows every shell command (read-only, mutating, restricted,
operators, substitution) and only rejects genuinely empty input.
"""

from __future__ import annotations

import pytest

from config.constants.repl_autonomy import AutoLevel
from tools.interactive_shell.shared import apply_auto_level, apply_plan_only_gate
from tools.interactive_shell.shell.policy import evaluate_shell_command


def test_shell_is_allow_before_confirmation_gate() -> None:
    r = evaluate_shell_command("pwd")
    assert r.verdict == "allow"
    assert r.tool_type == "shell"
    assert r.shell_classification == "unrestricted"


def test_restricted_shell_is_allow() -> None:
    """Alpha mode removed the restricted deny floor; ``sudo`` now runs."""
    r = evaluate_shell_command("sudo ls /")
    assert r.verdict == "allow"
    assert r.shell_classification == "unrestricted"


def test_operator_shell_is_allow() -> None:
    """Shell operators run through a shell instead of being blocked."""
    r = evaluate_shell_command("ls | grep x")
    assert r.verdict == "allow"


def test_mutating_shell_is_allow() -> None:
    r = evaluate_shell_command("rm -rf /tmp/x")
    assert r.verdict == "allow"
    assert r.shell_classification == "unrestricted"


def test_passthrough_shell_is_allow() -> None:
    r = evaluate_shell_command("!echo hi")
    assert r.verdict == "allow"


def test_empty_shell_input_is_deny() -> None:
    """Only genuinely empty input is rejected (input validation, not a guardrail)."""
    r = evaluate_shell_command("!")
    assert r.verdict == "deny"


def test_nmap_cidr_is_allow_in_alpha() -> None:
    """Alpha has no shell deny floor — a CIDR scan is not a policy exception."""
    r = evaluate_shell_command("nmap -n -p 22 --open 10.0.0.0/24")
    assert r.verdict == "allow"


@pytest.mark.parametrize("command", ["ls", "sort ~ input", "rm victim"])
def test_shell_approval_never_depends_on_read_only_text(command: str) -> None:
    result = evaluate_shell_command(command)

    assert apply_auto_level(result, AutoLevel.HIGH).verdict == "allow"
    for level in (AutoLevel.OFF, AutoLevel.LOW, AutoLevel.MED):
        assert apply_auto_level(result, level).verdict == "ask"
    assert apply_plan_only_gate(result, plan_only_active=True).verdict == "ask"

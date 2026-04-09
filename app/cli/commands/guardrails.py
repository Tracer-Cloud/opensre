"""CLI commands for managing sensitive information guardrail rules."""

from __future__ import annotations

import click


@click.group()
def guardrails() -> None:
    """Manage sensitive information guardrail rules."""


@guardrails.command(name="init")
def guardrails_init() -> None:
    """Create a starter guardrails config with common patterns."""
    from app.guardrails.cli import cmd_init

    cmd_init()


@guardrails.command(name="test")
@click.argument("text")
def guardrails_test(text: str) -> None:
    """Test guardrail rules against a text string."""
    from app.guardrails.cli import cmd_test

    cmd_test(text)


@guardrails.command()
@click.option("--limit", "-n", default=50, help="Number of recent entries to show.")
def audit(limit: int) -> None:
    """Show recent guardrail audit log entries."""
    from app.guardrails.cli import cmd_audit

    cmd_audit(limit=limit)


@guardrails.command(name="rules")
def guardrails_rules() -> None:
    """List configured guardrail rules."""
    from app.guardrails.cli import cmd_rules

    cmd_rules()

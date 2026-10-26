"""CLI commands for managing sensitive information guardrail rules."""

from __future__ import annotations

import json

import click

from infrastructure.process.runtime_flags import is_json_output


@click.group()
def guardrails() -> None:
    """Manage sensitive information guardrail rules."""


@guardrails.command(name="init")
def guardrails_init() -> None:
    """Create a starter guardrails config with common patterns."""
    from infrastructure.safety.guardrails.cli import cmd_init

    cmd_init()


@guardrails.command(name="test")
@click.argument("text")
def guardrails_test(text: str) -> None:
    """Test guardrail rules against a text string."""
    from infrastructure.safety.guardrails.cli import cmd_test

    cmd_test(text)


@guardrails.command()
@click.option("--limit", "-n", default=50, help="Number of recent entries to show.")
def audit(limit: int) -> None:
    """Show recent guardrail audit log entries."""
    from infrastructure.safety.guardrails.cli import cmd_audit

    cmd_audit(limit=limit)


@guardrails.command(name="rules")
def guardrails_rules() -> None:
    """List configured guardrail rules."""
    if is_json_output():
        from infrastructure.safety.guardrails.rules import get_default_rules_path, load_rules

        click.echo(
            json.dumps(
                [
                    {
                        "name": rule.name,
                        "action": rule.action.value,
                        "patterns": [pattern.pattern for pattern in rule.patterns],
                        "keywords": list(rule.keywords),
                        "description": rule.description,
                        "replacement": rule.replacement,
                        "enabled": rule.enabled,
                    }
                    for rule in load_rules(get_default_rules_path())
                ]
            )
        )
        return
    from infrastructure.safety.guardrails.cli import cmd_rules

    cmd_rules()

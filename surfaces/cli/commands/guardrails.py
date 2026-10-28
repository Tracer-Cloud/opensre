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
    if is_json_output():
        from infrastructure.safety.guardrails.evaluator import GuardrailEvaluator
        from infrastructure.safety.guardrails.rules import get_default_rules_path, load_rules

        rules_path = get_default_rules_path()
        if not rules_path.exists():
            click.echo(
                json.dumps(
                    {
                        "config_found": False,
                        "config_path": str(rules_path),
                        "matches": [],
                        "blocked": False,
                        "blocking_rules": [],
                        "redacted_output": None,
                    }
                )
            )
            return

        rules = load_rules(rules_path)
        if not rules:
            click.echo(
                json.dumps(
                    {
                        "config_found": True,
                        "config_path": str(rules_path),
                        "matches": [],
                        "blocked": False,
                        "blocking_rules": [],
                        "redacted_output": None,
                        "error": "no_valid_rules",
                    }
                )
            )
            return

        result = GuardrailEvaluator(rules).scan(text)
        matches = [
            {
                "rule_name": match.rule_name,
                "action": match.action.value,
                "matched_text": match.matched_text,
                "start": match.start,
                "end": match.end,
            }
            for match in result.matches
        ]
        redacted_output = None
        if result.matches and not result.blocked:
            redact_matches = sorted(
                (m for m in result.matches if m.action.value == "redact"),
                key=lambda m: m.start,
                reverse=True,
            )
            redacted = text
            for match in redact_matches:
                redacted = (
                    redacted[: match.start]
                    + f"[REDACTED:{match.rule_name}]"
                    + redacted[match.end :]
                )
            redacted_output = redacted

        click.echo(
            json.dumps(
                {
                    "config_found": True,
                    "config_path": str(rules_path),
                    "matches": matches,
                    "blocked": result.blocked,
                    "blocking_rules": list(result.blocking_rules),
                    "redacted_output": redacted_output,
                }
            )
        )
        return

    from infrastructure.safety.guardrails.cli import cmd_test

    cmd_test(text)


@guardrails.command()
@click.option("--limit", "-n", default=50, help="Number of recent entries to show.")
def audit(limit: int) -> None:
    """Show recent guardrail audit log entries."""
    if is_json_output():
        from infrastructure.safety.guardrails.audit import AuditLogger

        entries = AuditLogger().read_entries(limit=limit)
        click.echo(json.dumps({"limit": limit, "count": len(entries), "entries": entries}))
        return

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

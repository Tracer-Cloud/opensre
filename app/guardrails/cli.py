"""CLI command implementations for ``opensre guardrails``."""

from __future__ import annotations

import json

import click

from app.guardrails.audit import AuditLogger
from app.guardrails.engine import GuardrailEngine
from app.guardrails.rules import get_default_rules_path, load_rules

_STARTER_CONFIG = """\
# OpenSRE Guardrails Configuration
# Rules are evaluated against all text sent to LLMs.
# Actions: redact (mask the value), block (reject the request), audit (log and allow).

rules:
  - name: aws_access_key
    description: "AWS access key IDs (AKIA...)"
    action: redact
    patterns:
      - "(?:AKIA|ASIA)[A-Z0-9]{16}"

  - name: aws_secret_key
    description: "AWS secret access keys (40-char base64)"
    action: redact
    patterns:
      - "(?i)aws_secret_access_key[\\\\s=:]+[A-Za-z0-9/+=]{40}"

  - name: credit_card
    description: "Credit card numbers (13-19 digits with optional separators)"
    action: block
    patterns:
      - "\\\\b\\\\d{4}[- ]?\\\\d{4}[- ]?\\\\d{4}[- ]?\\\\d{4}\\\\b"

  - name: private_key
    description: "PEM-encoded private keys"
    action: block
    patterns:
      - "-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"

  - name: generic_api_token
    description: "Common API token patterns in key=value assignments"
    action: redact
    patterns:
      - "(?i)(?:api_key|api_token|auth_token|access_token|secret_key)[\\\\s=:]+[A-Za-z0-9_\\\\-]{20,}"
"""


def cmd_init() -> None:
    """Create a starter guardrails.yml with common patterns."""
    rules_path = get_default_rules_path()
    if rules_path.exists():
        click.echo(f"  Guardrails config already exists at {rules_path}")
        click.echo("  Use 'opensre guardrails rules' to view current rules.")
        return

    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(_STARTER_CONFIG, encoding="utf-8")
    click.echo(f"  Created starter guardrails config at {rules_path}")
    click.echo("  Edit the file to customize rules for your environment.")
    click.echo("  Test with: opensre guardrails test 'AKIAIOSFODNN7EXAMPLE'")


def cmd_test(text: str) -> None:
    """Scan sample text and display what would be matched/redacted/blocked."""
    rules_path = get_default_rules_path()
    if not rules_path.exists():
        click.echo(f"  No guardrails config found at {rules_path}")
        click.echo("  Create the file to enable guardrails.")
        return

    rules = load_rules(rules_path)
    if not rules:
        click.echo("  No valid rules loaded from config.")
        return

    engine = GuardrailEngine(rules)
    result = engine.scan(text)

    if not result.matches:
        click.echo("  No matches found.")
        return

    for match in result.matches:
        click.echo(
            f"  [{match.action.value.upper()}] {match.rule_name}: matched '{match.matched_text}'"
        )

    if result.blocked:
        click.echo(f"\n  BLOCKED by: {', '.join(result.blocking_rules)}")
    else:
        redact_matches = sorted(
            (m for m in result.matches if m.action.value == "redact"),
            key=lambda m: m.start,
            reverse=True,
        )
        redacted = text
        for match in redact_matches:
            redacted = (
                redacted[: match.start] + f"[REDACTED:{match.rule_name}]" + redacted[match.end :]
            )
        click.echo(f"\n  Redacted output: {redacted}")


def cmd_audit(*, limit: int = 50) -> None:
    """Print recent audit log entries."""
    audit_logger = AuditLogger()
    entries = audit_logger.read_entries(limit=limit)

    if not entries:
        click.echo("  No audit entries found.")
        return

    for entry in entries:
        click.echo(json.dumps(entry))


def cmd_rules() -> None:
    """List all configured guardrail rules."""
    rules_path = get_default_rules_path()
    if not rules_path.exists():
        click.echo(f"  No guardrails config at {rules_path}")
        return

    rules = load_rules(rules_path)
    if not rules:
        click.echo("  No valid rules found in config.")
        return

    for rule in rules:
        status = "enabled" if rule.enabled else "disabled"
        patterns_count = len(rule.patterns)
        keywords_count = len(rule.keywords)
        click.echo(
            f"  {rule.name:<30} {rule.action.value:<8} {status:<10} "
            f"patterns={patterns_count} keywords={keywords_count}"
        )
        if rule.description:
            click.echo(f"    {rule.description}")

"""CLI command implementations for ``opensre guardrails``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn

import click

from infrastructure.safety.guardrails.audit import AuditLogger
from infrastructure.safety.guardrails.evaluator import GuardrailEvaluator, ScanMatch, ScanResult
from infrastructure.safety.guardrails.rules import get_default_rules_path, load_rules

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


def _redact_text(text: str, matches: tuple[ScanMatch, ...]) -> str:
    """Replace every redact-action match span with a ``[REDACTED:<rule>]`` marker."""
    redact_matches = sorted(
        (m for m in matches if m.action.value == "redact"),
        key=lambda m: m.start,
        reverse=True,
    )
    redacted = text
    for match in redact_matches:
        redacted = redacted[: match.start] + f"[REDACTED:{match.rule_name}]" + redacted[match.end :]
    return redacted


def _exit_json_error(message: str, rules_path: Path) -> NoReturn:
    """Emit a machine-readable error object and exit non-zero (``--json`` mode)."""
    click.echo(json.dumps({"error": message, "config_path": str(rules_path)}))
    raise SystemExit(1)


def _echo_test_json(text: str, result: ScanResult, rules_path: Path) -> None:
    """Emit the ``guardrails test`` result as one JSON object.

    ``redacted`` is ``null`` when the text is blocked, since a blocked request
    is rejected rather than rewritten.
    """
    payload = {
        "config_path": str(rules_path),
        "matches": [
            {
                "rule": m.rule_name,
                "action": m.action.value,
                "matched_text": m.matched_text,
                "start": m.start,
                "end": m.end,
            }
            for m in result.matches
        ],
        "blocked": result.blocked,
        "blocking_rules": list(result.blocking_rules),
        "redacted": None if result.blocked else _redact_text(text, result.matches),
    }
    click.echo(json.dumps(payload))


def cmd_test(text: str, *, json_output: bool = False) -> None:
    """Scan sample text and display what would be matched/redacted/blocked.

    With ``json_output`` a missing config or an empty rule set is an error:
    a JSON error object is printed and the process exits 1.
    """
    rules_path = get_default_rules_path()
    if not rules_path.exists():
        if json_output:
            _exit_json_error("No guardrails config found.", rules_path)
        click.echo(f"  No guardrails config found at {rules_path}")
        click.echo("  Create the file to enable guardrails.")
        return

    rules = load_rules(rules_path)
    if not rules:
        if json_output:
            _exit_json_error("No valid rules loaded from config.", rules_path)
        click.echo("  No valid rules loaded from config.")
        return

    evaluator = GuardrailEvaluator(rules)
    result = evaluator.scan(text)

    if json_output:
        _echo_test_json(text, result, rules_path)
        return

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
        click.echo(f"\n  Redacted output: {_redact_text(text, result.matches)}")


def cmd_audit(*, limit: int = 50, json_output: bool = False) -> None:
    """Print recent audit log entries.

    Text mode prints one JSON object per line; ``json_output`` prints a single
    JSON array so the output parses as one document (``[]`` when empty).
    """
    audit_logger = AuditLogger()
    entries = audit_logger.read_entries(limit=limit)

    if json_output:
        click.echo(json.dumps(entries))
        return

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

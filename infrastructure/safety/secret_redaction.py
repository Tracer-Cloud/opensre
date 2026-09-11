"""Redact known credential patterns from local diagnostic and history text."""

from __future__ import annotations

from config.secret_redaction import DEFAULT_REDACTION_RULES, RedactionRule


def redact_text(text: str, rules: tuple[RedactionRule, ...] = DEFAULT_REDACTION_RULES) -> str:
    """Apply each rule's pattern in declared order, replacing every match."""
    for rule in rules:
        text = rule.pattern.sub(rule.replacement, text)
    return text

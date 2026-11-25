"""Redact known credential patterns from diagnostic text and structured payloads."""

from __future__ import annotations

import re
from collections.abc import Callable

from config.secret_redaction import DEFAULT_REDACTION_RULES, RedactionRule

__all__ = ["redact_text", "redact_text_keeping_lines", "redact_values"]


def redact_text(text: str, rules: tuple[RedactionRule, ...] = DEFAULT_REDACTION_RULES) -> str:
    """Apply each rule's pattern in declared order, replacing every match."""
    for rule in rules:
        text = rule.pattern.sub(rule.replacement, text)
    return text


def redact_text_keeping_lines(
    text: str, rules: tuple[RedactionRule, ...] = DEFAULT_REDACTION_RULES
) -> str:
    """Redact like :func:`redact_text`, leaving every other line on its own number.

    A private key spans many lines and its placeholder is one. The line breaks
    a match held are put back after its placeholder, so text shown with line
    numbers still cites the file's.
    """
    for rule in rules:
        text = rule.pattern.sub(_with_its_line_breaks(rule.replacement), text)
    return text


def _with_its_line_breaks(replacement: str) -> Callable[[re.Match[str]], str]:
    def replace(match: re.Match[str]) -> str:
        return match.expand(replacement) + "\n" * match.group(0).count("\n")

    return replace


def redact_values(value: object, *, redactor: Callable[[str], str] = redact_text) -> object:
    """Copy an acyclic JSON-oriented value, redacting strings before serialization.

    Keys and other objects become redacted strings; tuples become lists.
    ``redactor`` replaces the default credential policy for every string.
    """
    if isinstance(value, str):
        return redactor(value)
    if isinstance(value, dict):
        return {
            redactor(str(key)): redact_values(item, redactor=redactor)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_values(item, redactor=redactor) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redactor(str(value))

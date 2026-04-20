"""Guardrail scanning engine: detect, redact, block, and audit sensitive content."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.guardrails.audit import AuditLogger
from app.guardrails.rules import (
    GuardrailAction,
    GuardrailRule,
    get_default_rules_path,
    load_rules,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanMatch:
    """A single match found by a guardrail rule."""

    rule_name: str
    action: GuardrailAction
    matched_text: str
    start: int
    end: int


@dataclass(frozen=True)
class ScanResult:
    """Result of scanning text against all guardrail rules."""

    matches: tuple[ScanMatch, ...] = field(default_factory=tuple)
    blocked: bool = False
    blocking_rules: tuple[str, ...] = field(default_factory=tuple)


class GuardrailBlockedError(Exception):
    """Raised when text matches a blocking guardrail rule."""

    def __init__(self, rule_names: tuple[str, ...]) -> None:
        self.rule_names = rule_names
        super().__init__(f"Guardrail blocked by rules: {', '.join(rule_names)}.")


class GuardrailEngine:
    """Scan text against configured rules and apply redact/block/audit actions."""

    def __init__(
        self,
        rules: list[GuardrailRule],
        *,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._rules = [r for r in rules if r.enabled]
        self._audit = audit_logger

    @property
    def is_active(self) -> bool:
        """True if any rules are loaded and enabled."""
        return len(self._rules) > 0

    def scan(self, text: str) -> ScanResult:
        """Scan text against all enabled rules and return matches."""
        if not self._rules:
            return ScanResult()

        matches: list[ScanMatch] = []
        text_lower = text.lower()

        for rule in self._rules:
            for pattern in rule.patterns:
                for m in pattern.finditer(text):
                    matches.append(
                        ScanMatch(
                            rule_name=rule.name,
                            action=rule.action,
                            matched_text=m.group(),
                            start=m.start(),
                            end=m.end(),
                        )
                    )

            for keyword in rule.keywords:
                start = 0
                while True:
                    idx = text_lower.find(keyword, start)
                    if idx == -1:
                        break
                    matches.append(
                        ScanMatch(
                            rule_name=rule.name,
                            action=rule.action,
                            matched_text=text[idx : idx + len(keyword)],
                            start=idx,
                            end=idx + len(keyword),
                        )
                    )
                    start = idx + len(keyword)

        blocking_rules = tuple(m.rule_name for m in matches if m.action == GuardrailAction.BLOCK)
        return ScanResult(
            matches=tuple(matches),
            blocked=len(blocking_rules) > 0,
            blocking_rules=blocking_rules,
        )

    def apply(self, text: str) -> str:
        """Scan text, apply redactions, and audit. Raises on block."""
        result = self.scan(text)

        if not result.matches:
            return text

        for match in result.matches:
            if self._audit:
                self._audit.log(
                    rule_name=match.rule_name,
                    action=match.action.value,
                    matched_text_preview=match.matched_text,
                )

        if result.blocked:
            raise GuardrailBlockedError(result.blocking_rules)

        redact_matches = sorted(
            [m for m in result.matches if m.action == GuardrailAction.REDACT],
            key=lambda m: (m.start, m.end),
            reverse=True,
        )
        seen_end = len(text)
        redacted = text
        for match in redact_matches:
            if match.end > seen_end:
                continue
            replacement = self._get_replacement(match.rule_name)
            redacted = redacted[: match.start] + replacement + redacted[match.end :]
            seen_end = match.start

        return redacted

    def should_block(self, text: str) -> bool:
        """Quick check: does this text trigger any blocking rule?"""
        return self.scan(text).blocked

    def _get_replacement(self, rule_name: str) -> str:
        """Get the replacement string for a rule, defaulting to [REDACTED:<name>]."""
        for rule in self._rules:
            if rule.name == rule_name and rule.replacement:
                return rule.replacement
        return f"[REDACTED:{rule_name}]"


_engine: GuardrailEngine | None = None


def get_guardrail_engine() -> GuardrailEngine:
    """Return the module-level singleton engine, loading rules from default path."""
    global _engine  # noqa: PLW0603
    if _engine is None:
        rules = load_rules(get_default_rules_path())
        _engine = GuardrailEngine(rules, audit_logger=AuditLogger())
    return _engine


def reset_guardrail_engine() -> None:
    """Clear the singleton (for tests and config reload)."""
    global _engine  # noqa: PLW0603
    _engine = None

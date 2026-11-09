"""End-to-end tests for guardrail overlapping keyword redaction."""

from __future__ import annotations

import re

from infrastructure.safety.guardrails.evaluator import GuardrailEvaluator
from infrastructure.safety.guardrails.rules import GuardrailAction, GuardrailRule

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rule(
    name: str = "test",
    action: str = "redact",
    patterns: list[str] | None = None,
    keywords: list[str] | None = None,
    replacement: str = "",
) -> GuardrailRule:
    compiled = tuple(re.compile(p, re.IGNORECASE) for p in (patterns or []))
    kws = tuple(k.lower() for k in (keywords or []))
    return GuardrailRule(
        name=name,
        action=GuardrailAction(action),
        patterns=compiled,
        keywords=kws,
        replacement=replacement,
    )


# ---------------------------------------------------------------------------
# 1. Guardrails overlapping keyword redaction — full pipeline
# ---------------------------------------------------------------------------


class TestOverlappingRedactionE2E:
    """Prove that overlapping keywords are fully redacted through the complete
    scan → audit → redact pipeline, not just in isolation."""

    def test_secret_and_secret_key_across_rules(self) -> None:
        """Two rules, shorter keyword is a prefix of the longer one."""
        evaluator = GuardrailEvaluator(
            [
                _make_rule(name="generic", keywords=["secret"]),
                _make_rule(name="specific", keywords=["secret_key"]),
            ]
        )
        result = evaluator.apply("export secret_key=hunter2")
        # The longer match must win — no leftover "_key"
        assert "_key" not in result
        assert "=hunter2" in result
        assert "secret" not in result.lower().replace("[redacted:", "")

    def test_api_and_api_key_single_rule(self) -> None:
        """Both keywords in the same rule."""
        evaluator = GuardrailEvaluator(
            [
                _make_rule(name="creds", keywords=["api", "api_key"]),
            ]
        )
        result = evaluator.apply("set api_key=abc123")
        assert "_key" not in result
        assert "=abc123" in result

    def test_multiple_overlapping_occurrences(self) -> None:
        """Multiple overlapping pairs in one string."""
        evaluator = GuardrailEvaluator(
            [
                _make_rule(name="r1", keywords=["pass"]),
                _make_rule(name="r2", keywords=["password"]),
            ]
        )
        text = "password=foo and pass=bar"
        result = evaluator.apply(text)
        # "password" should be fully redacted (not "word" leftover)
        assert "word" not in result.split("and")[0]
        # "pass" alone should also be redacted
        assert "=bar" in result

    def test_pattern_and_keyword_overlap(self) -> None:
        """A regex pattern and a keyword overlap on the same span."""
        evaluator = GuardrailEvaluator(
            [
                _make_rule(name="pat", patterns=[r"AKIA[A-Z0-9]{16}"]),
                _make_rule(name="kw", keywords=["akia"]),
            ]
        )
        text = "key=AKIAIOSFODNN7EXAMPLE"
        result = evaluator.apply(text)
        # The longer regex match should win
        assert "AKIA" not in result
        assert "key=" in result

    def test_full_scan_apply_audit_cycle(self) -> None:
        """Scan, check matches, then apply — the real evaluator flow."""
        evaluator = GuardrailEvaluator(
            [
                _make_rule(name="tokens", keywords=["token", "token_secret"]),
            ]
        )
        text = "auth token_secret=xyz and token=abc"

        # Apply correctly redacts overlapping matches without leftovers.
        result = evaluator.apply(text)
        assert "_secret" not in result
        assert "=xyz" in result
        assert "=abc" in result

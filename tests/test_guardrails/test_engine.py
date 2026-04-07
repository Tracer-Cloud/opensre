from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from app.guardrails.audit import AuditLogger
from app.guardrails.engine import (
    GuardrailBlockedError,
    GuardrailEngine,
    get_guardrail_engine,
    reset_guardrail_engine,
)
from app.guardrails.rules import GuardrailAction, GuardrailRule


def _rule(
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


class TestScan:
    def test_no_rules_returns_empty(self) -> None:
        engine = GuardrailEngine([])
        result = engine.scan("anything")
        assert result.matches == ()
        assert result.blocked is False

    def test_pattern_match_returns_positions(self) -> None:
        engine = GuardrailEngine([_rule(patterns=["AKIA[0-9A-Z]{16}"])])
        result = engine.scan("key=AKIAIOSFODNN7EXAMPLE here")
        assert len(result.matches) == 1
        assert result.matches[0].matched_text == "AKIAIOSFODNN7EXAMPLE"
        assert result.matches[0].start == 4

    def test_keyword_match_case_insensitive(self) -> None:
        engine = GuardrailEngine([_rule(keywords=["secret-host"])])
        result = engine.scan("Connecting to SECRET-HOST now")
        assert len(result.matches) == 1
        assert result.matches[0].matched_text == "SECRET-HOST"

    def test_multiple_matches_same_text(self) -> None:
        engine = GuardrailEngine([
            _rule(name="r1", patterns=["\\d{4}"]),
        ])
        result = engine.scan("codes 1234 and 5678")
        assert len(result.matches) == 2

    def test_no_match_returns_empty(self) -> None:
        engine = GuardrailEngine([_rule(patterns=["AKIA[0-9A-Z]{16}"])])
        result = engine.scan("nothing sensitive here")
        assert result.matches == ()

    def test_block_action_sets_blocked(self) -> None:
        engine = GuardrailEngine([_rule(action="block", keywords=["forbidden"])])
        result = engine.scan("this is forbidden data")
        assert result.blocked is True
        assert "test" in result.blocking_rules

    def test_redact_does_not_set_blocked(self) -> None:
        engine = GuardrailEngine([_rule(action="redact", keywords=["secret"])])
        result = engine.scan("my secret value")
        assert result.blocked is False

    def test_disabled_rule_ignored(self) -> None:
        rule = GuardrailRule(
            name="disabled",
            action=GuardrailAction.BLOCK,
            keywords=("danger",),
            enabled=False,
        )
        engine = GuardrailEngine([rule])
        result = engine.scan("danger ahead")
        assert result.matches == ()


class TestApply:
    def test_redacts_matched_text(self) -> None:
        engine = GuardrailEngine([_rule(patterns=["AKIA[0-9A-Z]{16}"])])
        result = engine.apply("key=AKIAIOSFODNN7EXAMPLE")
        assert "AKIA" not in result
        assert "[REDACTED:test]" in result

    def test_uses_custom_replacement(self) -> None:
        engine = GuardrailEngine([
            _rule(patterns=["\\d{16}"], replacement="[CC_MASKED]"),
        ])
        result = engine.apply("card 4111111111111111")
        assert "[CC_MASKED]" in result

    def test_preserves_unmatched_text(self) -> None:
        engine = GuardrailEngine([_rule(keywords=["secret"])])
        result = engine.apply("before secret after")
        assert result.startswith("before ")
        assert result.endswith(" after")

    def test_multiple_redactions(self) -> None:
        engine = GuardrailEngine([_rule(patterns=["\\bpassword\\b"])])
        result = engine.apply("password one and password two")
        assert "password" not in result
        assert result.count("[REDACTED:test]") == 2

    def test_raises_on_block(self) -> None:
        engine = GuardrailEngine([_rule(action="block", keywords=["forbidden"])])
        with pytest.raises(GuardrailBlockedError, match="test"):
            engine.apply("this is forbidden")

    def test_audit_action_passes_through(self) -> None:
        engine = GuardrailEngine([_rule(action="audit", keywords=["monitored"])])
        result = engine.apply("this is monitored text")
        assert result == "this is monitored text"

    def test_no_match_returns_original(self) -> None:
        engine = GuardrailEngine([_rule(patterns=["xyz123"])])
        text = "clean text here"
        assert engine.apply(text) == text

    def test_audit_logger_called_on_match(self, tmp_path: Path) -> None:
        audit = AuditLogger(path=tmp_path / "audit.jsonl")
        engine = GuardrailEngine(
            [_rule(action="redact", keywords=["secret"])],
            audit_logger=audit,
        )
        engine.apply("my secret data")
        entries = audit.read_entries()
        assert len(entries) == 1
        assert entries[0]["rule_name"] == "test"

    def test_audit_logger_called_on_block(self, tmp_path: Path) -> None:
        audit = AuditLogger(path=tmp_path / "audit.jsonl")
        engine = GuardrailEngine(
            [_rule(action="block", keywords=["danger"])],
            audit_logger=audit,
        )
        with pytest.raises(GuardrailBlockedError):
            engine.apply("danger zone")
        entries = audit.read_entries()
        assert len(entries) == 1


class TestEdgeCases:
    def test_empty_string(self) -> None:
        engine = GuardrailEngine([_rule(keywords=["secret"])])
        assert engine.apply("") == ""
        assert engine.scan("").matches == ()

    def test_unicode_content(self) -> None:
        engine = GuardrailEngine([_rule(keywords=["password"])])
        text = "my password is p@\u00e9\u00df\u00f1"
        result = engine.apply(text)
        assert "[REDACTED:test]" in result

    def test_overlapping_keyword_matches(self) -> None:
        engine = GuardrailEngine([
            _rule(name="r1", action="redact", keywords=["secret"]),
            _rule(name="r2", action="redact", keywords=["secret_key"]),
        ])
        result = engine.scan("my secret_key here")
        assert len(result.matches) >= 2

    def test_multiple_rules_on_same_span(self) -> None:
        engine = GuardrailEngine([
            _rule(name="r1", action="audit", keywords=["token"]),
            _rule(name="r2", action="redact", keywords=["token"]),
        ])
        result = engine.apply("my token value")
        assert "[REDACTED:r2]" in result

    def test_very_long_text(self) -> None:
        engine = GuardrailEngine([_rule(patterns=["AKIA[A-Z0-9]{16}"])])
        text = "x" * 10000 + "AKIAIOSFODNN7EXAMPLE" + "y" * 10000
        result = engine.apply(text)
        assert "AKIA" not in result
        assert result.startswith("x" * 10000)
        assert result.endswith("y" * 10000)

    def test_pattern_at_start_and_end(self) -> None:
        engine = GuardrailEngine([_rule(patterns=["\\bsecret\\b"])])
        assert "[REDACTED" in engine.apply("secret")
        assert engine.apply("secret").startswith("[REDACTED")

    def test_adjacent_matches(self) -> None:
        engine = GuardrailEngine([_rule(patterns=["\\d{4}"])])
        result = engine.apply("12345678")
        assert "1234" not in result
        assert "5678" not in result

    def test_mixed_block_and_redact(self) -> None:
        engine = GuardrailEngine([
            _rule(name="blocker", action="block", keywords=["forbidden"]),
            _rule(name="redactor", action="redact", keywords=["secret"]),
        ])
        with pytest.raises(GuardrailBlockedError):
            engine.apply("secret and forbidden")

    def test_case_insensitive_pattern(self) -> None:
        engine = GuardrailEngine([_rule(patterns=["akia[a-z0-9]{16}"])])
        result = engine.scan("key=AKIAIOSFODNN7EXAMPLE")
        assert len(result.matches) == 1


class TestShouldBlock:
    def test_true_for_block_rule(self) -> None:
        engine = GuardrailEngine([_rule(action="block", keywords=["nope"])])
        assert engine.should_block("nope") is True

    def test_false_for_redact_rule(self) -> None:
        engine = GuardrailEngine([_rule(action="redact", keywords=["fine"])])
        assert engine.should_block("fine") is False

    def test_false_for_no_match(self) -> None:
        engine = GuardrailEngine([_rule(action="block", keywords=["xyz"])])
        assert engine.should_block("clean") is False


class TestIsActive:
    def test_false_when_no_rules(self) -> None:
        assert GuardrailEngine([]).is_active is False

    def test_true_when_rules_loaded(self) -> None:
        assert GuardrailEngine([_rule()]).is_active is True

    def test_false_when_all_disabled(self) -> None:
        rule = GuardrailRule(
            name="off", action=GuardrailAction.REDACT, keywords=("x",), enabled=False
        )
        assert GuardrailEngine([rule]).is_active is False


class TestSingleton:
    def test_get_returns_engine(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        config = tmp_path / "guardrails.yml"
        config.write_text(yaml.dump({"rules": [
            {"name": "t", "action": "audit", "keywords": ["test"]},
        ]}), encoding="utf-8")
        monkeypatch.setattr("app.guardrails.engine.get_default_rules_path", lambda: config)
        reset_guardrail_engine()

        engine = get_guardrail_engine()
        assert engine.is_active is True

    def test_reset_clears_singleton(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(
            "app.guardrails.engine.get_default_rules_path",
            lambda: tmp_path / "missing.yml",
        )
        reset_guardrail_engine()
        engine = get_guardrail_engine()
        assert engine.is_active is False
        reset_guardrail_engine()

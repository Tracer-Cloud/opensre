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
        engine = GuardrailEngine(
            [
                _rule(name="r1", patterns=["\\d{4}"]),
            ]
        )
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
        engine = GuardrailEngine(
            [
                _rule(patterns=["\\d{16}"], replacement="[CC_MASKED]"),
            ]
        )
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

    def test_audit_logger_records_every_match_even_when_merged(self, tmp_path: Path) -> None:
        """Match-level audit must record every match even when the output
        only shows a single merged redaction. Guarantees that a reviewer
        tracing audit → output can account for every rule that fired."""
        audit = AuditLogger(path=tmp_path / "audit.jsonl")
        engine = GuardrailEngine(
            [
                _rule(name="long", action="redact", keywords=["super_secret_token_value"]),
                _rule(name="short", action="redact", keywords=["secret_token"]),
            ],
            audit_logger=audit,
        )
        out = engine.apply("data super_secret_token_value end")
        # Single merged redaction in output
        assert out == "data [REDACTED:long] end"
        # But both matches recorded in audit
        entries = audit.read_entries()
        assert len(entries) == 2
        recorded_rules = sorted(e["rule_name"] for e in entries)
        assert recorded_rules == ["long", "short"]
        previews = sorted(e["matched_text_preview"] for e in entries)
        assert previews == ["secret_token", "super_secret_token_value"]


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
        engine = GuardrailEngine(
            [
                _rule(name="r1", action="redact", keywords=["secret"]),
                _rule(name="r2", action="redact", keywords=["secret_key"]),
            ]
        )
        result = engine.scan("my secret_key here")
        assert len(result.matches) >= 2

    def test_overlapping_keyword_redaction_prefers_longest(self) -> None:
        """Overlapping keywords at the same start must redact the longest match."""
        engine = GuardrailEngine(
            [
                _rule(name="r1", action="redact", keywords=["secret"]),
                _rule(name="r2", action="redact", keywords=["secret_key"]),
            ]
        )
        result = engine.apply("my secret_key=xyz")
        # The longer keyword "secret_key" should win; no leftover "_key"
        assert "_key" not in result
        assert "=xyz" in result

    def test_overlapping_keyword_same_rule_redaction(self) -> None:
        """Overlapping keywords within a single rule must redact the longest match."""
        engine = GuardrailEngine(
            [
                _rule(name="r1", action="redact", keywords=["api", "api_key"]),
            ]
        )
        result = engine.apply("my api_key=123")
        assert "_key" not in result
        assert "=123" in result

    def test_contained_span_redacts_union_no_leak(self) -> None:
        """A shorter match fully contained in a longer match must not leave the
        longer match's prefix/suffix unredacted.

        Regression: the prior seen_end walk processed matches right-to-left
        and skipped any match whose end exceeded the cursor, so with rules
        matching ``super_secret_token_value`` (wide) and ``secret_token``
        (contained), the ``super_`` and ``_value`` bookends survived.
        """
        engine = GuardrailEngine(
            [
                _rule(name="long", action="redact", keywords=["super_secret_token_value"]),
                _rule(name="short", action="redact", keywords=["secret_token"]),
            ]
        )
        result = engine.apply("data super_secret_token_value end")
        assert "super_" not in result
        assert "_value" not in result
        assert "secret" not in result.lower()
        assert result == "data [REDACTED:long] end"

    def test_contained_span_uses_longest_rule_name(self) -> None:
        """When one match fully contains another, the union redaction carries
        the wider rule's name, not the inner one's."""
        engine = GuardrailEngine(
            [
                _rule(name="wide", action="redact", keywords=["aaa_bbb_ccc_ddd_eee"]),
                _rule(name="inner", action="redact", keywords=["ccc"]),
            ]
        )
        result = engine.apply("xx aaa_bbb_ccc_ddd_eee yy")
        assert "[REDACTED:wide]" in result
        assert "[REDACTED:inner]" not in result

    def test_partial_overlap_redacts_union(self) -> None:
        """Two partially-overlapping matches neither contained in the other
        must produce one redaction covering the full union span."""
        engine = GuardrailEngine(
            [
                _rule(name="a", action="redact", keywords=["abcdefghij"]),  # [0:10]
                _rule(name="b", action="redact", keywords=["fghijklmno"]),  # [5:15]
            ]
        )
        result = engine.apply("abcdefghijklmno tail")
        # Neither raw keyword nor its tail survives; a single redaction
        # covers the union [0:15].
        for frag in ("abcde", "fghij", "klmno"):
            assert frag not in result
        assert result.count("[REDACTED:") == 1
        assert result.endswith("tail")

    def test_disjoint_matches_stay_separate(self) -> None:
        """Non-overlapping matches produce independent redactions with the
        unaffected text between them preserved verbatim."""
        engine = GuardrailEngine(
            [
                _rule(name="a", action="redact", keywords=["foo"]),
                _rule(name="b", action="redact", keywords=["bar"]),
            ]
        )
        result = engine.apply("foo middle bar")
        assert result == "[REDACTED:a] middle [REDACTED:b]"

    def test_adjacent_matches_not_merged(self) -> None:
        """Matches that touch at exactly one offset (end of A == start of B)
        must remain separate redactions — they do not actually overlap."""
        engine = GuardrailEngine(
            [
                _rule(name="a", action="redact", keywords=["foo"]),
                _rule(name="b", action="redact", keywords=["bar"]),
            ]
        )
        result = engine.apply("foobar rest")
        assert result == "[REDACTED:a][REDACTED:b] rest"

    def test_three_way_chain_of_overlaps_redacts_single_union(self) -> None:
        """Transitive overlap A∩B, B∩C but A⊥C still collapses to one span."""
        engine = GuardrailEngine(
            [
                _rule(name="a", action="redact", keywords=["abcdefg"]),  # [0:7]
                _rule(name="b", action="redact", keywords=["efghijk"]),  # [4:11]
                _rule(name="c", action="redact", keywords=["ijklmno"]),  # [8:15]
            ]
        )
        result = engine.apply("abcdefghijklmno tail")
        assert result.count("[REDACTED:") == 1
        assert result.endswith("tail")
        # No keyword fragments leak out either end.
        for frag in ("abcd", "hijk", "lmno"):
            assert frag not in result

    def test_contained_pattern_with_wider_keyword_preserves_wider_name(self) -> None:
        """Mixing a regex pattern and a keyword on the same span behaves the
        same as two keywords — the wider match wins."""
        engine = GuardrailEngine(
            [
                _rule(name="pat_short", action="redact", patterns=[r"\d{4}"]),
                _rule(name="kw_wide", action="redact", keywords=["cc_1234_xyz"]),
            ]
        )
        result = engine.apply("pay cc_1234_xyz now")
        assert "[REDACTED:kw_wide]" in result
        assert "[REDACTED:pat_short]" not in result
        assert "1234" not in result
        assert "cc_" not in result and "_xyz" not in result

    def test_real_world_api_key_and_aws_access_key_overlap(self) -> None:
        """Exercises the bug class with the exact patterns shipped in the
        ``_STARTER_CONFIG`` of ``app/guardrails/cli.py``. The
        ``aws_access_key`` pattern ``(?:AKIA|ASIA)[A-Z0-9]{16}`` is a strict
        substring of the ``generic_api_token`` pattern
        ``(api_key|...|secret_key)[\\s=:]+[A-Za-z0-9_\\-]{20,}`` when the value
        is itself an AWS access key. The pre-fix output leaked the
        ``api_key=`` prefix; the merged output fully redacts both the label
        and the key."""
        engine = GuardrailEngine(
            [
                _rule(
                    name="aws_access_key",
                    action="redact",
                    patterns=[r"(?:AKIA|ASIA)[A-Z0-9]{16}"],
                ),
                _rule(
                    name="generic_api_token",
                    action="redact",
                    patterns=[
                        r"(?i)(?:api_key|api_token|auth_token|access_token|secret_key)"
                        r"[\s=:]+[A-Za-z0-9_\-]{20,}"
                    ],
                ),
            ]
        )
        text = "config: api_key=AKIAIOSFODNN7EXAMPLE"
        result = engine.apply(text)
        assert "AKIA" not in result
        assert "api_key=" not in result  # ← pre-fix leaked this
        assert "IOSFOD" not in result
        assert result == "config: [REDACTED:generic_api_token]"

    def test_real_world_aws_secret_key_contains_aws_access_key(self) -> None:
        """The shipped ``aws_secret_key`` pattern matches both the literal
        ``aws_secret_access_key`` label and 40 subsequent chars, so a value
        that happens to start with an AWS access key produces two redact
        matches with containment. Pre-fix, the wider ``aws_secret_key`` span
        was dropped and the label + tail leaked."""
        engine = GuardrailEngine(
            [
                _rule(
                    name="aws_access_key",
                    action="redact",
                    patterns=[r"(?:AKIA|ASIA)[A-Z0-9]{16}"],
                ),
                _rule(
                    name="aws_secret_key",
                    action="redact",
                    patterns=[r"(?i)aws_secret_access_key[\s=:]+[A-Za-z0-9/+=]{40}"],
                ),
            ]
        )
        text = "export aws_secret_access_key=AKIAIOSFODNN7EXAMPLEabcdefghijklmnopqrst"
        result = engine.apply(text)
        assert "aws_secret_access_key" not in result  # ← pre-fix leaked the label
        assert "AKIA" not in result
        assert "abcdefghij" not in result  # ← pre-fix leaked the 40-char tail
        assert result == "export [REDACTED:aws_secret_key]"

    def test_multiple_rules_on_same_span(self) -> None:
        engine = GuardrailEngine(
            [
                _rule(name="r1", action="audit", keywords=["token"]),
                _rule(name="r2", action="redact", keywords=["token"]),
            ]
        )
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
        engine = GuardrailEngine(
            [
                _rule(name="blocker", action="block", keywords=["forbidden"]),
                _rule(name="redactor", action="redact", keywords=["secret"]),
            ]
        )
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
        config.write_text(
            yaml.dump(
                {
                    "rules": [
                        {"name": "t", "action": "audit", "keywords": ["test"]},
                    ]
                }
            ),
            encoding="utf-8",
        )
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

from __future__ import annotations

from pathlib import Path

import yaml

from app.guardrails.rules import GuardrailAction, load_rules


def _write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "guardrails.yml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


class TestLoadRules:
    def test_returns_empty_when_file_missing(self, tmp_path: Path) -> None:
        assert load_rules(tmp_path / "nonexistent.yml") == []

    def test_returns_empty_for_malformed_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "guardrails.yml"
        path.write_text(": : : bad yaml {{", encoding="utf-8")
        assert load_rules(path) == []

    def test_returns_empty_when_rules_key_missing(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {"version": 1})
        assert load_rules(path) == []

    def test_parses_pattern_rule(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {"rules": [
            {"name": "aws_key", "action": "redact", "patterns": ["AKIA[0-9A-Z]{16}"]},
        ]})
        rules = load_rules(path)
        assert len(rules) == 1
        assert rules[0].name == "aws_key"
        assert rules[0].action == GuardrailAction.REDACT
        assert len(rules[0].patterns) == 1

    def test_parses_keyword_rule(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {"rules": [
            {"name": "internal", "action": "audit", "keywords": ["prod-db.internal"]},
        ]})
        rules = load_rules(path)
        assert len(rules) == 1
        assert rules[0].keywords == ("prod-db.internal",)

    def test_parses_mixed_pattern_and_keyword(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {"rules": [
            {
                "name": "mixed",
                "action": "redact",
                "patterns": ["\\bsecret\\b"],
                "keywords": ["api_key"],
            },
        ]})
        rules = load_rules(path)
        assert len(rules) == 1
        assert len(rules[0].patterns) == 1
        assert rules[0].keywords == ("api_key",)

    def test_skips_rule_with_invalid_regex(self, tmp_path: Path) -> None:
        bad_pattern = "[invalid"
        path = _write_config(tmp_path, {"rules": [
            {"name": "bad_regex", "action": "redact", "patterns": [bad_pattern]},
        ]})
        rules = load_rules(path)
        assert len(rules) == 0

    def test_skips_rule_with_no_patterns_or_keywords(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {"rules": [
            {"name": "empty", "action": "redact"},
        ]})
        assert load_rules(path) == []

    def test_skips_rule_with_invalid_action(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {"rules": [
            {"name": "bad", "action": "explode", "patterns": ["test"]},
        ]})
        assert load_rules(path) == []

    def test_respects_enabled_false(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {"rules": [
            {"name": "disabled", "action": "redact", "patterns": ["test"], "enabled": False},
        ]})
        rules = load_rules(path)
        assert len(rules) == 1
        assert rules[0].enabled is False

    def test_defaults_action_to_audit(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {"rules": [
            {"name": "default_action", "keywords": ["sensitive"]},
        ]})
        rules = load_rules(path)
        assert rules[0].action == GuardrailAction.AUDIT

    def test_parses_description_and_replacement(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {"rules": [
            {
                "name": "cc",
                "action": "redact",
                "patterns": ["\\d{16}"],
                "description": "Credit cards",
                "replacement": "[CC_REDACTED]",
            },
        ]})
        rules = load_rules(path)
        assert rules[0].description == "Credit cards"
        assert rules[0].replacement == "[CC_REDACTED]"

    def test_multiple_rules(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, {"rules": [
            {"name": "r1", "action": "redact", "patterns": ["aaa"]},
            {"name": "r2", "action": "block", "keywords": ["bbb"]},
            {"name": "r3", "action": "audit", "keywords": ["ccc"]},
        ]})
        rules = load_rules(path)
        assert len(rules) == 3
        assert [r.action for r in rules] == [
            GuardrailAction.REDACT,
            GuardrailAction.BLOCK,
            GuardrailAction.AUDIT,
        ]

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.guardrails.audit import AuditLogger
from app.guardrails.cli import cmd_audit, cmd_init, cmd_rules, cmd_test


class TestCmdInit:
    def test_creates_starter_config(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rules_path = tmp_path / "guardrails.yml"
        monkeypatch.setattr("app.guardrails.cli.get_default_rules_path", lambda: rules_path)

        cmd_init()
        out = capsys.readouterr().out
        assert "Created starter" in out
        assert rules_path.exists()

        content = rules_path.read_text(encoding="utf-8")
        assert "aws_access_key" in content
        assert "credit_card" in content
        assert "private_key" in content

    def test_does_not_overwrite_existing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rules_path = tmp_path / "guardrails.yml"
        rules_path.write_text("existing content", encoding="utf-8")
        monkeypatch.setattr("app.guardrails.cli.get_default_rules_path", lambda: rules_path)

        cmd_init()
        out = capsys.readouterr().out
        assert "already exists" in out
        assert rules_path.read_text(encoding="utf-8") == "existing content"


class TestCmdTest:
    def test_no_config_shows_message(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            "app.guardrails.cli.get_default_rules_path",
            lambda: tmp_path / "missing.yml",
        )
        cmd_test("any text")
        assert "No guardrails config" in capsys.readouterr().out

    def test_shows_matches(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        config = tmp_path / "guardrails.yml"
        config.write_text(
            yaml.dump(
                {
                    "rules": [
                        {"name": "aws_key", "action": "redact", "patterns": ["AKIA[0-9A-Z]{16}"]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("app.guardrails.cli.get_default_rules_path", lambda: config)

        cmd_test("key=AKIAIOSFODNN7EXAMPLE")
        out = capsys.readouterr().out
        assert "REDACT" in out
        assert "aws_key" in out
        assert "Redacted output" in out

    def test_shows_block_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        config = tmp_path / "guardrails.yml"
        config.write_text(
            yaml.dump(
                {
                    "rules": [
                        {"name": "danger", "action": "block", "keywords": ["forbidden"]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("app.guardrails.cli.get_default_rules_path", lambda: config)

        cmd_test("this is forbidden data")
        out = capsys.readouterr().out
        assert "BLOCKED" in out
        assert "danger" in out

    def test_no_matches_shows_clean(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        config = tmp_path / "guardrails.yml"
        config.write_text(
            yaml.dump(
                {
                    "rules": [
                        {"name": "r1", "action": "redact", "patterns": ["AKIA[A-Z0-9]{16}"]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("app.guardrails.cli.get_default_rules_path", lambda: config)

        cmd_test("nothing sensitive")
        assert "No matches" in capsys.readouterr().out


class TestCmdRules:
    def test_lists_configured_rules(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        config = tmp_path / "guardrails.yml"
        config.write_text(
            yaml.dump(
                {
                    "rules": [
                        {
                            "name": "aws_key",
                            "action": "redact",
                            "patterns": ["AKIA"],
                            "description": "AWS keys",
                        },
                        {"name": "cc", "action": "block", "patterns": ["\\d{16}"]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("app.guardrails.cli.get_default_rules_path", lambda: config)

        cmd_rules()
        out = capsys.readouterr().out
        assert "aws_key" in out
        assert "redact" in out
        assert "AWS keys" in out
        assert "cc" in out
        assert "block" in out

    def test_no_config_shows_message(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            "app.guardrails.cli.get_default_rules_path",
            lambda: tmp_path / "missing.yml",
        )
        cmd_rules()
        assert "No guardrails config" in capsys.readouterr().out


class TestCmdAudit:
    def test_shows_entries(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        audit = AuditLogger(path=tmp_path / "audit.jsonl")
        audit.log(rule_name="r1", action="redact", matched_text_preview="secret")
        monkeypatch.setattr("app.guardrails.cli.AuditLogger", lambda: audit)

        cmd_audit(limit=10)
        out = capsys.readouterr().out
        assert "r1" in out
        assert "redact" in out

    def test_empty_shows_message(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        audit = AuditLogger(path=tmp_path / "empty.jsonl")
        monkeypatch.setattr("app.guardrails.cli.AuditLogger", lambda: audit)

        cmd_audit(limit=10)
        assert "No audit entries" in capsys.readouterr().out

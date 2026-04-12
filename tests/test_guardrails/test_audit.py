from __future__ import annotations

import json
from pathlib import Path

from app.guardrails.audit import AuditLogger


class TestAuditLogger:
    def test_creates_file_on_first_log(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        logger.log(rule_name="test", action="redact", matched_text_preview="secret")
        assert log_path.exists()

    def test_appends_jsonl_entries(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        logger.log(rule_name="r1", action="redact", matched_text_preview="a")
        logger.log(rule_name="r2", action="block", matched_text_preview="b")

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["rule_name"] == "r1"
        assert json.loads(lines[1])["rule_name"] == "r2"

    def test_entry_has_expected_fields(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        logger.log(
            rule_name="cc", action="redact", matched_text_preview="4111", context="llm_invoke"
        )

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["rule_name"] == "cc"
        assert entry["action"] == "redact"
        assert entry["matched_text_preview"] == "4111"
        assert entry["context"] == "llm_invoke"
        assert "timestamp" in entry

    def test_truncates_matched_text_preview(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        long_text = "x" * 100
        logger.log(rule_name="test", action="audit", matched_text_preview=long_text)

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert len(entry["matched_text_preview"]) == 40

    def test_read_entries_returns_most_recent(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        for i in range(10):
            logger.log(rule_name=f"r{i}", action="audit", matched_text_preview=f"m{i}")

        entries = logger.read_entries(limit=3)
        assert len(entries) == 3
        assert entries[0]["rule_name"] == "r7"
        assert entries[2]["rule_name"] == "r9"

    def test_read_entries_empty_file(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        assert logger.read_entries() == []

    def test_read_entries_nonexistent_file(self, tmp_path: Path) -> None:
        logger = AuditLogger(path=tmp_path / "missing.jsonl")
        assert logger.read_entries() == []

    def test_handles_write_failure_gracefully(self, tmp_path: Path) -> None:
        log_path = tmp_path / "readonly" / "audit.jsonl"
        # Make parent read-only
        (tmp_path / "readonly").mkdir()
        (tmp_path / "readonly").chmod(0o444)
        logger = AuditLogger(path=log_path)
        # Should not raise
        logger.log(rule_name="test", action="redact", matched_text_preview="x")
        # Restore permissions for cleanup
        (tmp_path / "readonly").chmod(0o755)

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        log_path = tmp_path / "deep" / "nested" / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        logger.log(rule_name="test", action="audit", matched_text_preview="data")
        assert log_path.exists()

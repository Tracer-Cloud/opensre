from __future__ import annotations

from pathlib import Path

import pytest

from tests.benchmarks.toolcall_model_benchmark.readme_updater import (
    extract_summary_from_report,
    update_readme_benchmarks,
)
from tests.benchmarks.toolcall_model_benchmark.readme_updater import (
    main as updater_main,
)

SAMPLE_README = """\
# Project

Some intro text.

## Benchmark

<!-- BENCHMARK-START -->
_No benchmark results yet._
<!-- BENCHMARK-END -->

## Other Section

More content here.
"""

SNIPPET = """\
| Scenario | Status |
|---|---|
| 001-replication-lag | ok |

**1/1 passed**"""

SAMPLE_REPORT = """\
# OpenSRE Benchmark

Scope: 001-replication-lag, 002-connection-exhaustion.
Metrics reported: duration, token usage, estimated LLM cost.

## Per-case Metrics

| Scenario | Status | Duration (s) | Input Tokens | Output Tokens | Total Tokens | Est. Cost (USD) | Error |
|---|---|---:|---:|---:|---:|---:|---|
| 001-replication-lag | ok | 12.50 | 5000 | 1500 | 6500 | 0.021000 |  |
| 002-connection-exhaustion | error | 0.00 | 0 | 0 | 0 | 0.000000 | timeout |

## Summary

- Cases: 2
- Successful runs: 1
- Failed runs: 1
- Total duration (s): 12.50
- Avg duration (s): 6.25

## Notes

- This is an operational benchmark report.
"""


def _write_readme(tmp_path: Path, content: str = SAMPLE_README) -> Path:
    readme = tmp_path / "README.md"
    readme.write_text(content, encoding="utf-8")
    return readme


class TestUpdateReadmeBenchmarks:
    def test_replaces_content_between_markers(self, tmp_path: Path) -> None:
        readme = _write_readme(tmp_path)
        update_readme_benchmarks(readme, SNIPPET)

        result = readme.read_text(encoding="utf-8")
        assert "<!-- BENCHMARK-START -->" in result
        assert "<!-- BENCHMARK-END -->" in result
        assert SNIPPET in result
        assert "_No benchmark results yet._" not in result

    def test_idempotent(self, tmp_path: Path) -> None:
        readme = _write_readme(tmp_path)
        update_readme_benchmarks(readme, SNIPPET)
        first = readme.read_text(encoding="utf-8")

        update_readme_benchmarks(readme, SNIPPET)
        second = readme.read_text(encoding="utf-8")

        assert first == second

    def test_raises_when_markers_missing(self, tmp_path: Path) -> None:
        readme = _write_readme(tmp_path, "# No markers here\n")

        with pytest.raises(ValueError, match="Start marker"):
            update_readme_benchmarks(readme, SNIPPET)

    def test_raises_when_only_start_marker(self, tmp_path: Path) -> None:
        readme = _write_readme(tmp_path, "<!-- BENCHMARK-START -->\ncontent\n")

        with pytest.raises(ValueError, match="End marker"):
            update_readme_benchmarks(readme, SNIPPET)

    def test_preserves_surrounding_content(self, tmp_path: Path) -> None:
        readme = _write_readme(tmp_path)
        update_readme_benchmarks(readme, SNIPPET)
        result = readme.read_text(encoding="utf-8")

        assert "# Project" in result
        assert "Some intro text." in result
        assert "## Other Section" in result
        assert "More content here." in result

    def test_handles_empty_between_markers(self, tmp_path: Path) -> None:
        content = "before\n<!-- BENCHMARK-START --><!-- BENCHMARK-END -->\nafter\n"
        readme = _write_readme(tmp_path, content)
        update_readme_benchmarks(readme, SNIPPET)

        result = readme.read_text(encoding="utf-8")
        assert SNIPPET in result
        assert "before" in result
        assert "after" in result


class TestExtractSummaryFromReport:
    def test_extracts_table_and_summary(self) -> None:
        snippet = extract_summary_from_report(SAMPLE_REPORT)

        assert "| Scenario |" in snippet
        assert "001-replication-lag" in snippet
        assert "002-connection-exhaustion" in snippet
        assert "1/2 passed" in snippet
        assert "docs/benchmarks/results.md" in snippet

    def test_uses_compact_format(self) -> None:
        """Both update paths must produce the same compact 5-column table."""
        snippet = extract_summary_from_report(SAMPLE_REPORT)
        assert "| Scenario | Status | Duration (s) | Tokens | Est. Cost (USD) |" in snippet
        # Must NOT contain the verbose 8-column header from results.md
        assert "Input Tokens" not in snippet

    def test_includes_link_to_full_report(self) -> None:
        snippet = extract_summary_from_report(SAMPLE_REPORT)
        assert "[docs/benchmarks/results.md]" in snippet

    def test_handles_empty_report(self) -> None:
        snippet = extract_summary_from_report("")
        assert "docs/benchmarks/results.md" in snippet


class TestUpdaterMainEntryPoint:
    """Integration tests for the standalone ``main()`` entry point."""

    def test_updates_readme_from_cached_results(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Set up directory structure mirroring the repo
        readme = tmp_path / "README.md"
        readme.write_text(SAMPLE_README, encoding="utf-8")

        benchmarks_dir = tmp_path / "docs" / "benchmarks"
        benchmarks_dir.mkdir(parents=True)
        results = benchmarks_dir / "results.md"
        results.write_text(SAMPLE_REPORT, encoding="utf-8")

        # Patch _find_repo_root to return our tmp_path
        monkeypatch.setattr(
            "tests.benchmarks.toolcall_model_benchmark.readme_updater._find_repo_root",
            lambda: tmp_path,
        )

        exit_code = updater_main()
        assert exit_code == 0

        content = readme.read_text(encoding="utf-8")
        assert "001-replication-lag" in content
        assert "002-connection-exhaustion" in content
        assert "docs/benchmarks/results.md" in content
        assert "_No benchmark results yet._" not in content

    def test_returns_error_when_no_results(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        readme = tmp_path / "README.md"
        readme.write_text(SAMPLE_README, encoding="utf-8")

        monkeypatch.setattr(
            "tests.benchmarks.toolcall_model_benchmark.readme_updater._find_repo_root",
            lambda: tmp_path,
        )

        exit_code = updater_main()
        assert exit_code == 1

    def test_idempotent_via_main(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        readme = tmp_path / "README.md"
        readme.write_text(SAMPLE_README, encoding="utf-8")

        benchmarks_dir = tmp_path / "docs" / "benchmarks"
        benchmarks_dir.mkdir(parents=True)
        (benchmarks_dir / "results.md").write_text(SAMPLE_REPORT, encoding="utf-8")

        monkeypatch.setattr(
            "tests.benchmarks.toolcall_model_benchmark.readme_updater._find_repo_root",
            lambda: tmp_path,
        )

        updater_main()
        first = readme.read_text(encoding="utf-8")

        updater_main()
        second = readme.read_text(encoding="utf-8")

        assert first == second

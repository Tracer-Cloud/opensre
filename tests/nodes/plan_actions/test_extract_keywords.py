"""Tests for keyword extraction used by action planning."""

from __future__ import annotations

from app.nodes.plan_actions.extract_keywords import extract_keywords


def test_extract_keywords_matches_problem_and_alert_text() -> None:
    assert extract_keywords(
        "Pipeline failed with memory error",
        "PipelineFailure",
    ) == ["memory", "failure", "failed", "error", "pipeline"]


def test_extract_keywords_matches_alert_name_only() -> None:
    assert extract_keywords("", "BatchJobTimeout") == ["timeout", "batch", "job"]


def test_extract_keywords_matches_problem_text_only() -> None:
    assert extract_keywords("Database timeout with slow queries", "") == [
        "timeout",
        "slow",
        "database",
    ]


def test_extract_keywords_returns_empty_list_when_no_keywords_match() -> None:
    assert extract_keywords("No issues detected", "Success") == []


def test_extract_keywords_returns_empty_list_for_empty_input() -> None:
    assert extract_keywords("", "") == []


def test_extract_keywords_does_not_duplicate_repeated_matches() -> None:
    assert extract_keywords(
        "memory memory memory error",
        "MemoryError",
    ) == ["memory", "error"]


def test_extract_keywords_matches_case_insensitively() -> None:
    assert extract_keywords("CPU FAILURE", "DiskError") == [
        "failure",
        "error",
        "cpu",
        "disk",
    ]

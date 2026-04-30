"""Tests for the shared truncation utility."""

import pytest

from app.utils.truncation import truncate


@pytest.mark.parametrize(
    "text,limit,suffix,expected",
    [
        # text shorter than limit — returned unchanged
        ("short", 10, "...", "short"),
        # text exactly at limit — returned unchanged
        ("exactly10c", 10, "...", "exactly10c"),
        # text longer than limit — truncated with suffix
        ("this is a long string", 10, "...", "this is..."),
        # limit equals suffix length — suffix returned as-is
        ("long", 3, "...", "..."),
        # unicode suffix
        ("unicode text", 5, "…", "unic…"),
        # no change needed
        ("no change needed", 100, "...", "no change needed"),
    ],
)
def test_truncate(text: str, limit: int, suffix: str, expected: str) -> None:
    assert truncate(text, limit, suffix=suffix) == expected


def test_truncate_result_length_equals_limit() -> None:
    result = truncate("hello world", 8)
    assert result == "hello..."
    assert len(result) == 8


def test_truncate_unicode_suffix() -> None:
    result = truncate("hello world", 8, suffix="…")
    assert result.endswith("…")
    assert len(result) == 8


def test_truncate_text_at_exact_limit_not_truncated() -> None:
    text = "a" * 10
    assert truncate(text, 10) == text


def test_truncate_empty_string() -> None:
    assert truncate("", 5) == ""


def test_truncate_limit_smaller_than_suffix_returns_clipped_suffix() -> None:
    # limit=2, suffix="..." (len 3) — cannot fit suffix, return suffix[:limit]
    result = truncate("hello", 2, suffix="...")
    assert result == ".."
    assert len(result) == 2


def test_truncate_limit_zero_returns_empty_string() -> None:
    result = truncate("hello", 0, suffix="...")
    assert result == ""
    assert len(result) == 0


def test_truncate_limit_one_with_long_suffix() -> None:
    result = truncate("hello", 1, suffix="...")
    assert result == "."
    assert len(result) == 1


def test_truncate_suffix_longer_than_text_but_within_limit() -> None:
    # text shorter than limit — no truncation even if suffix is long
    result = truncate("hi", 10, suffix=".....")
    assert result == "hi"


def test_truncate_very_long_text() -> None:
    result = truncate("x" * 10_000, 100)
    assert result.endswith("...")
    assert len(result) == 100


def test_truncate_empty_suffix() -> None:
    result = truncate("hello world", 5, suffix="")
    assert result == "hello"
    assert len(result) == 5

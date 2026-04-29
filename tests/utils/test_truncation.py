"""Tests for the shared truncation utility."""

import pytest

from app.utils.truncation import truncate


@pytest.mark.parametrize(
    "text,limit,ellipsis,expected",
    [
        ("short", 10, "...", "short"),
        ("exactly10c", 10, "...", "exactly10c"),
        ("this is a long string", 10, "...", "this is..."),
        ("long", 3, "...", "..."),
        ("unicode", 5, "…", "unic…"),
        ("no change needed", 100, "...", "no change needed"),
    ],
)
def test_truncate(text: str, limit: int, ellipsis: str, expected: str) -> None:
    assert truncate(text, limit, ellipsis=ellipsis) == expected


def test_truncate_default_ellipsis() -> None:
    result = truncate("hello world", 8)
    assert result == "hello..."
    assert len(result) == 8


def test_truncate_unicode_ellipsis() -> None:
    result = truncate("hello world", 8, ellipsis="…")
    assert result.endswith("…")
    assert len(result) == 8


def test_truncate_text_at_exact_limit_not_truncated() -> None:
    text = "a" * 10
    assert truncate(text, 10) == text

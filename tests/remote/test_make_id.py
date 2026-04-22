"""Tests for _make_id() and _slugify() functions."""

import pytest

from app.remote.server import _make_id, _slugify


class TestSlugify:
    """Tests for _slugify() function."""

    def test_empty_string(self):
        """Empty string returns empty slug."""
        assert _slugify("") == ""

    def test_whitespace_only(self):
        """Whitespace-only string returns empty slug."""
        assert _slugify("   ") == ""
        assert _slugify("\t") == ""
        assert _slugify("\n") == ""

    def test_normal_text(self):
        """Normal text is lowercased and separated by hyphens."""
        assert _slugify("Test Alert") == "test-alert"
        assert _slugify("My Alert Name") == "my-alert-name"

    def test_special_characters(self):
        """Special characters are replaced with hyphens."""
        assert _slugify("test@alert!") == "test-alert"
        assert _slugify("alert#123") == "alert-123"

    def test_leading_trailing_spaces(self):
        """Leading and trailing spaces are stripped."""
        assert _slugify("  alert  ") == "alert"
        assert _slugify("  test alert  ") == "test-alert"

    def test_max_length(self):
        """Slug is truncated to 60 characters."""
        long_text = "a" * 100
        assert len(_slugify(long_text)) == 60

    def test_numbers_preserved(self):
        """Numbers are preserved in slug."""
        assert _slugify("alert123") == "alert123"
        assert _slugify("test2026") == "test2026"


class TestMakeId:
    """Tests for _make_id() function."""

    def test_empty_alert_name_uses_fallback(self):
        """Empty alert_name uses 'investigation' as fallback."""
        result = _make_id("")
        assert result.endswith("_investigation")
        assert result.startswith("20")  # Starts with timestamp year

    def test_whitespace_alert_name_uses_fallback(self):
        """Whitespace-only alert_name uses 'investigation' as fallback."""
        result = _make_id("   ")
        assert result.endswith("_investigation")

    def test_normal_alert_name(self):
        """Normal alert_name is slugified and appended."""
        result = _make_id("Test Alert")
        assert "test-alert" in result

    def test_format(self):
        """ID format is YYYYMMDD_HHMMSS_slug."""
        import re
        result = _make_id("test")
        # Pattern: 20260421_123456_test
        pattern = r"^\d{8}_\d{6}_[a-z0-9-]+$"
        assert re.match(pattern, result), f"ID '{result}' doesn't match expected format"

    def test_unique_timestamps(self):
        """Different calls produce different IDs (due to timestamp)."""
        # Note: This test may rarely fail if called within same second
        import time
        result1 = _make_id("test")
        time.sleep(1.1)  # Ensure different second
        result2 = _make_id("test")
        assert result1 != result2

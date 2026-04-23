"""Tests for app.remote.server utility functions."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.remote.server import _make_id, _safe_investigation_path, _slugify


def test_slugify_converts_text_to_url_safe_format() -> None:
    """Test that _slugify converts special characters to hyphens and lowercases text."""
    result = _slugify("CPU High Usage at 90%")
    assert result == "cpu-high-usage-at-90"


def test_slugify_handles_multiple_special_characters() -> None:
    """Test that consecutive special characters are collapsed to single hyphen."""
    result = _slugify("Error!!! Database---Failed")
    assert result == "error-database-failed"


def test_slugify_trims_hyphens_from_edges() -> None:
    """Test that leading/trailing hyphens are removed."""
    result = _slugify("---test-alert---")
    assert result == "test-alert"


def test_slugify_handles_empty_string() -> None:
    """Test that empty string produces empty result."""
    result = _slugify("")
    assert result == ""


def test_slugify_handles_whitespace_only() -> None:
    """Test that whitespace-only string produces empty result after stripping."""
    result = _slugify("   ")
    assert result == ""


def test_make_id_generates_timestamp_with_slug() -> None:
    """Test that _make_id combines timestamp with slugified alert name."""
    result = _make_id("Database Connection Failed")
    # Format: YYYYMMDD_HHMMSS_slug - verify the timestamp structure
    parts = result.split("_")
    assert len(parts) >= 3  # date, time, slug
    assert len(parts[0]) == 8  # YYYYMMDD
    assert parts[0].isdigit()
    assert len(parts[1]) == 6  # HHMMSS
    assert parts[1].isdigit()
    assert "_database-connection-failed" in result


def test_make_id_uses_investigation_fallback_for_empty_alert_name() -> None:
    """Test that empty alert name uses 'investigation' as fallback slug."""
    result = _make_id("")
    # Format: YYYYMMDD_HHMMSS_investigation
    parts = result.split("_")
    assert len(parts) >= 3
    assert len(parts[0]) == 8 and parts[0].isdigit()
    assert len(parts[1]) == 6 and parts[1].isdigit()
    assert result.endswith("_investigation")
    # Ensure no trailing underscore before investigation
    assert "_investigation" in result


def test_make_id_uses_investigation_fallback_for_whitespace_only() -> None:
    """Test that whitespace-only alert name uses 'investigation' as fallback."""
    result = _make_id("   ")
    parts = result.split("_")
    assert len(parts) >= 3
    assert len(parts[0]) == 8 and parts[0].isdigit()
    assert len(parts[1]) == 6 and parts[1].isdigit()
    assert result.endswith("_investigation")
    assert "_investigation" in result


def test_make_id_handles_special_characters_in_alert_name() -> None:
    """Test that special characters in alert name are properly slugified."""
    result = _make_id("API!!! Latency---High")
    parts = result.split("_")
    assert len(parts) >= 3
    assert len(parts[0]) == 8 and parts[0].isdigit()
    assert len(parts[1]) == 6 and parts[1].isdigit()
    assert "_api-latency-high" in result


def test_make_id_truncates_long_slugs() -> None:
    """Test that very long alert names are truncated to 60 characters in slug."""
    long_name = "Error " * 50  # Creates a very long string
    result = _make_id(long_name)
    parts = result.split("_", 2)  # Split into date, time, slug
    slug = parts[2]
    # Slug should be truncated to 60 chars
    assert len(slug) <= 60


def test_safe_investigation_path_accepts_valid_id() -> None:
    """Test that valid IDs are accepted and return a Path."""
    result = _safe_investigation_path("abc-123")
    assert result.name == "abc-123.md"


def test_safe_investigation_path_rejects_path_traversal_dotdot() -> None:
    """Test that ../x returns 400 Invalid investigation ID."""
    with pytest.raises(HTTPException) as exc_info:
        _safe_investigation_path("../x")
    assert exc_info.value.status_code == 400
    assert "Invalid investigation ID" in exc_info.value.detail


def test_safe_investigation_path_rejects_x_dotdot() -> None:
    """Test that x/.. returns 400 Invalid investigation ID."""
    with pytest.raises(HTTPException) as exc_info:
        _safe_investigation_path("x/..")
    assert exc_info.value.status_code == 400


def test_safe_investigation_path_rejects_x_md() -> None:
    """Test that x.md returns 400 Invalid investigation ID."""
    with pytest.raises(HTTPException) as exc_info:
        _safe_investigation_path("x.md")
    assert exc_info.value.status_code == 400


def test_safe_investigation_path_rejects_x_newline() -> None:
    """Test that x\\n returns 400 Invalid investigation ID."""
    with pytest.raises(HTTPException) as exc_info:
        _safe_investigation_path("x\n")
    assert exc_info.value.status_code == 400


def test_safe_investigation_path_rejects_empty() -> None:
    """Test that empty ID returns 400 Invalid investigation ID."""
    with pytest.raises(HTTPException) as exc_info:
        _safe_investigation_path("")
    assert exc_info.value.status_code == 400


def test_safe_investigation_path_rejects_special_chars() -> None:
    """Test that IDs with special characters return 400."""
    with pytest.raises(HTTPException) as exc_info:
        _safe_investigation_path("x$y")
    assert exc_info.value.status_code == 400


def test_safe_investigation_path_rejects_single_dot() -> None:
    """Test that single dot in ID returns 400."""
    with pytest.raises(HTTPException) as exc_info:
        _safe_investigation_path(".")
    assert exc_info.value.status_code == 400

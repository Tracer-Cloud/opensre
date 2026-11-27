"""Tests for core.domain.types.incident_anchors.

``parse_iso8601`` documents that it returns ``None`` for malformed input
"rather than raising", so a vendor payload that sends a timestamp as a
number, a list or a nested object must be treated as malformed — not as a
reason for the parser to raise.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.domain.types.incident_anchors import parse_iso8601


class TestParseIso8601:
    """Well-formed timestamps parse; everything else returns ``None``."""

    def test_parses_utc_timestamp(self) -> None:
        assert parse_iso8601("2026-01-02T03:04:05Z") == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    def test_treats_naive_timestamp_as_utc(self) -> None:
        assert parse_iso8601("2026-01-02T03:04:05") == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    def test_strips_surrounding_whitespace(self) -> None:
        assert parse_iso8601("  2026-01-02T03:04:05Z  ") == datetime(
            2026, 1, 2, 3, 4, 5, tzinfo=UTC
        )

    @pytest.mark.parametrize("value", ["", "   ", "not-a-date", "2026-13-45T99:99:99Z"])
    def test_malformed_string_returns_none(self, value: str) -> None:
        assert parse_iso8601(value) is None

    @pytest.mark.parametrize("value", [None, 0, 1_699_999_999, {"ts": 1}, ["2026-01-02"]])
    def test_non_string_input_returns_none_instead_of_raising(self, value: object) -> None:
        # Vendor payloads send epochs, nested objects and lists for the
        # timestamp field. The documented contract is ``None``, not an
        # exception escaping the parser.
        assert parse_iso8601(value) is None

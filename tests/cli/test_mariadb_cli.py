"""Tests for MariaDB CLI helpers."""

from __future__ import annotations

from app.integrations.cli import _parse_port


class TestParsePort:
    def test_valid_standard_port(self) -> None:
        assert _parse_port("3306") == 3306

    def test_valid_minimum_port(self) -> None:
        assert _parse_port("1") == 1

    def test_valid_maximum_port(self) -> None:
        assert _parse_port("65535") == 65535

    def test_invalid_zero_returns_default(self) -> None:
        assert _parse_port("0") == 3306

    def test_invalid_above_max_returns_default(self) -> None:
        assert _parse_port("65536") == 3306

    def test_invalid_negative_returns_default(self) -> None:
        assert _parse_port("-1") == 3306

    def test_non_numeric_returns_default(self) -> None:
        assert _parse_port("abc") == 3306

    def test_empty_string_returns_default(self) -> None:
        assert _parse_port("") == 3306

    def test_float_string_returns_default(self) -> None:
        assert _parse_port("33.06") == 3306

    def test_custom_default(self) -> None:
        assert _parse_port("bad", default=5432) == 5432

    def test_alternate_valid_port(self) -> None:
        assert _parse_port("3307") == 3307

"""Tests for the safe_int coercion helper."""

from __future__ import annotations

from typing import Any

import pytest

from infrastructure.text.coercion import safe_int


@pytest.mark.parametrize(
    "value,default,expected",
    [
        # int passes through unchanged
        (5, 0, 5),
        # zero is preserved
        (0, 99, 0),
        # negative int passes through unchanged
        (-7, 0, -7),
        # integer string parses to int
        ("25", 0, 25),
        # signed integer string parses
        ("-1", 0, -1),
        # integer with whitespace passes through
        ("  42  ", 0, 42),
    ],
)
def test_safe_int_returns_coerced_value(value: Any, default: int, expected: int) -> None:
    result = safe_int(value, default)
    assert result == expected
    assert type(result) is int


@pytest.mark.parametrize(
    "value",
    [
        # missing or unset
        None,
        # present but empty
        "",
        # non-numeric value
        "some string",
        # wrong type entirely
        object(),
        # decimal-shaped string
        "3.14",
    ],
)
def test_safe_int_returns_default_on_bad_input(value: Any) -> None:
    result = safe_int(value, 999)
    assert result == 999


@pytest.mark.parametrize(
    "value",
    [
        # float infinity raises OverflowError from int()
        float("inf"),
        float("-inf"),
    ],
)
def test_safe_int_returns_default_on_overflow(value: Any) -> None:
    result = safe_int(value, 999)
    assert result == 999


def test_safe_int_treats_bool_as_int_subclass() -> None:
    true_result = safe_int(True, 999)
    assert true_result == 1
    assert type(true_result) is int

    false_result = safe_int(False, 999)
    assert false_result == 0
    assert type(false_result) is int


def test_safe_int_truncates_float_toward_zero() -> None:
    positive_result = safe_int(3.9, 0)
    assert positive_result == 3
    assert type(positive_result) is int

    negative_result = safe_int(-3.9, 0)
    assert negative_result == -3
    assert type(negative_result) is int


def test_safe_int_returns_default_unchanged_for_negative_value() -> None:
    assert safe_int(None, -12) == -12


def test_safe_int_exception_domain() -> None:
    # TypeError + ValueError
    class _Raises:
        def __init__(self, exc: BaseException) -> None:
            self._exc = exc

        def __int__(self) -> int:
            raise self._exc

    assert safe_int(_Raises(TypeError("should get caught")), 111) == 111
    assert safe_int(_Raises(ValueError("should get caught")), 999) == 999
    assert safe_int(_Raises(OverflowError("should get caught")), 333) == 333

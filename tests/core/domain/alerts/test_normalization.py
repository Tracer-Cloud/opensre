"""Tests for alert payload normalization."""

from __future__ import annotations

from core.domain.alerts.normalization import _coerce_pid


def test_coerce_pid_accepts_int() -> None:
    assert _coerce_pid(1234) == 1234


def test_coerce_pid_accepts_numeric_string() -> None:
    assert _coerce_pid("1234") == 1234


def test_coerce_pid_rejects_negative() -> None:
    assert _coerce_pid(-1) is None


def test_coerce_pid_rejects_bool() -> None:
    """`bool` is a subclass of `int`, so True/False must not be treated as a PID."""
    assert _coerce_pid(True) is None
    assert _coerce_pid(False) is None


def test_coerce_pid_rejects_none_and_garbage() -> None:
    assert _coerce_pid(None) is None
    assert _coerce_pid("not-a-pid") is None

import pytest
from core.domain.alerts.normalization import _coerce_pid


def test_coerce_pid_rejects_booleans():
    """Ensure boolean values are not coerced into integers 1 or 0."""
    assert _coerce_pid(True) is None
    assert _coerce_pid(False) is None


def test_coerce_pid_valid_integers():
    """Ensure valid integer PIDs are parsed correctly."""
    assert _coerce_pid(1234) == 1234
    assert _coerce_pid("5678") == 5678


def test_coerce_pid_invalid_inputs():
    """Ensure invalid or non-PID inputs return None."""
    assert _coerce_pid(None) is None
    assert _coerce_pid("abc") is None

"""Unit tests for canonical alert payload normalization."""

from __future__ import annotations

import pytest

from core.domain.alerts.normalization import _coerce_pid, normalize_alert_payload


@pytest.mark.parametrize("raw", [True, False])
def test_boolean_pid_is_rejected(raw: bool) -> None:
    """``bool`` is an ``int`` subclass; a boolean PID is not a process id."""
    assert _coerce_pid(raw) is None


def test_boolean_pid_never_reaches_canonical_process() -> None:
    normalized = normalize_alert_payload({"pid": True, "labels": {"alertname": "X"}})

    assert normalized["canonical_alert"]["process"]["pid"] is None


def test_numeric_and_text_pids_are_preserved() -> None:
    assert _coerce_pid(4321) == 4321
    assert _coerce_pid("4321") == 4321

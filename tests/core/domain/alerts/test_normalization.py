"""Unit tests for canonical alert payload normalization."""

from __future__ import annotations

import pytest

from core.domain.alerts.normalization import _coerce_pid, normalize_alert_payload


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        (True, None),
        (False, None),
        (0, 0),
        (1234, 1234),
        (-1, None),
        (42.0, 42),
        (42.5, None),
        ("5678", 5678),
        ("0", 0),
        ("-50", None),
        ("not-a-number", None),
        ("", None),
        ("   ", None),
    ],
)
def test_coerce_pid(raw: object, expected: int | None) -> None:
    assert _coerce_pid(raw) == expected
    if expected is not None:
        assert type(_coerce_pid(raw)) is int


def test_normalize_alert_payload_ignores_boolean_pid() -> None:
    payload = {
        "alert_name": "HighMemoryUsage",
        "severity": "critical",
        "pid": True,
    }
    normalized = normalize_alert_payload(payload)
    assert "pid" not in normalized or normalized["pid"] is True
    assert normalized["canonical_alert"]["process"]["pid"] is None


def test_normalize_alert_payload_extracts_canonical_fields() -> None:
    payload = {
        "alert_name": "CrashLoopBackOff",
        "severity": "warning",
        "alert_source": "kubernetes",
        "process_name": "worker",
        "cmdline": "python -m worker",
        "pid": 4321,
        "tags": "env:production,region:us-east-1",
        "commonAnnotations": {"summary": "Pod restarting"},
    }
    normalized = normalize_alert_payload(payload)

    assert normalized["commonLabels"]["env"] == "production"
    assert normalized["commonLabels"]["region"] == "us-east-1"
    assert normalized["commonAnnotations"]["summary"] == "Pod restarting"

    canonical = normalized["canonical_alert"]
    assert canonical["schema"] == "opensre.alert.v1"
    assert canonical["alert_name"] == "CrashLoopBackOff"
    assert canonical["severity"] == "warning"
    assert canonical["alert_source"] == "kubernetes"
    assert canonical["process"] == {
        "name": "worker",
        "cmdline": "python -m worker",
        "pid": 4321,
    }

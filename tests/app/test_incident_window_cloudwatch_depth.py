"""Targeted regression test for the CloudWatch depth cap.

Pathologically deep ``{"Message": {"Message": ...}}`` payloads must not
recurse into stack overflow. The cap is enforced inside
``_cloudwatch_anchor`` and surfaced via the public ``resolve_incident_window``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.incident_window import (
    SOURCE_ACTIVATED_AT,
    SOURCE_DEFAULT,
    resolve_incident_window,
)

NOW = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)


def test_cloudwatch_legit_two_level_nesting_resolves() -> None:
    # SNS -> Message (parsed JSON) -> alarm dict with timestamp.
    payload = {
        "Message": {
            "alarm": {"StateUpdatedTimestamp": "2026-04-20T10:00:00Z"},
        }
    }
    result = resolve_incident_window(payload, now=NOW)
    assert result.source == SOURCE_ACTIVATED_AT


def test_cloudwatch_pathological_deep_nesting_does_not_recurse_forever() -> None:
    # Build a 200-level deep payload of {"Message": {"Message": ...}}.
    # Without the depth cap this would blow the Python recursion limit.
    deep: dict = {}
    cursor = deep
    for _ in range(200):
        cursor["Message"] = {}
        cursor = cursor["Message"]
    # No timestamp anywhere; resolver must fall back to default rather than crash.
    result = resolve_incident_window(deep, now=NOW)
    assert result.source == SOURCE_DEFAULT

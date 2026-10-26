"""Regression tests for ``resolve_incident_window`` payload anchoring.

The first test pins the P1 Greptile finding: a string-form webhook payload
must still be coerced and anchored on its ``startsAt``, not silently fall back
to a wall-clock window.

The last test confirms Grafana-shaped payloads still resolve to
``alert.startsAt`` after the dead ``_grafana_anchor`` shim was removed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from core.domain.types.incident_window import (
    SOURCE_DEFAULT,
    SOURCE_STARTS_AT,
    resolve_incident_window,
)

NOW = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)


def test_string_payload_resolves_via_coerce_dict() -> None:
    """A JSON-string raw_alert must be parsed and anchored correctly.

    The caller passes the original raw_alert, so resolve_incident_window must
    coerce the JSON string into a dict and find the timestamp itself.
    """
    payload_str = json.dumps(
        {
            "status": "firing",
            "alerts": [{"startsAt": "2026-04-20T09:00:00Z"}],
        }
    )
    result = resolve_incident_window(payload_str, now=NOW)
    assert result.source == SOURCE_STARTS_AT
    # 09:00 + 10min default buffer = 09:10
    assert result.until == datetime(2026, 4, 20, 9, 10, tzinfo=UTC)


def test_string_payload_with_no_timestamp_falls_back_to_default() -> None:
    """The fallback path is what the bug used to hit for every string
    payload. Confirm it still works when there is genuinely no anchor."""
    payload_str = json.dumps({"alertname": "noisy", "severity": "info"})
    result = resolve_incident_window(payload_str, now=NOW)
    assert result.source == SOURCE_DEFAULT


def test_grafana_payload_still_resolves_after_parser_removal() -> None:
    """Grafana managed alerts share Alertmanager's schema. The dedicated
    _grafana_anchor was a dead delegate to _alertmanager_anchor; removing
    it must not regress Grafana coverage. Today the Alertmanager parser
    handles both shapes."""
    payload = {
        "receiver": "grafana-default",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"grafana_folder": "Prod"},
                "startsAt": "2026-04-20T10:15:00Z",
            }
        ],
        "externalURL": "https://grafana.example.com",
    }
    result = resolve_incident_window(payload, now=NOW)
    assert result.source == SOURCE_STARTS_AT
    assert result.until == datetime(2026, 4, 20, 10, 25, tzinfo=UTC)

"""Evidence mapper tests for the Alertmanager tools."""

from typing import Any

import pytest

from core.tool_framework.utils import tool_unavailable
from integrations.alertmanager.tools import (
    _map_alertmanager_alerts,
    _map_alertmanager_silences,
)


def test_alerts_mapper_records_an_entry_counting_firing_alerts() -> None:
    # Arrange
    evidence: dict[str, Any] = {}
    output = {
        "source": "alertmanager",
        "available": True,
        "alerts": [
            {"status": "active", "labels": {"alertname": "HighCPU"}},
            {"status": "suppressed", "labels": {"alertname": "DiskFilling"}},
        ],
        "firing_alerts": [{"status": "active", "labels": {"alertname": "HighCPU"}}],
        "total": 2,
    }

    # Act
    _map_alertmanager_alerts(evidence, output, {})

    # Assert
    assert evidence["catalog_entries"] == [
        {
            "source": "alertmanager_alerts",
            "label": "Alertmanager Alerts",
            "summary": "2 alerts, 1 firing",
            "url": None,
            "snippet": None,
        }
    ]


def test_silences_mapper_records_an_entry_counting_active_silences() -> None:
    # Arrange
    evidence: dict[str, Any] = {}
    output = {
        "source": "alertmanager_silences",
        "available": True,
        "silences": [{"status": {"state": "active"}}, {"status": {"state": "expired"}}],
        "active_silences": [{"status": {"state": "active"}}],
        "total": 2,
    }

    # Act
    _map_alertmanager_silences(evidence, output, {})

    # Assert
    assert evidence["catalog_entries"] == [
        {
            "source": "alertmanager_silences",
            "label": "Alertmanager Silences",
            "summary": "2 silences, 1 active",
            "url": None,
            "snippet": None,
        }
    ]


@pytest.mark.parametrize(
    "output",
    [
        pytest.param({}, id="empty-payload"),
        pytest.param(
            tool_unavailable("alertmanager", "connection refused", alerts=[], total=0),
            id="unavailable-envelope",
        ),
        pytest.param({"source": "alertmanager", "available": True, "alerts": []}, id="no-alerts"),
    ],
)
def test_alerts_mapper_records_nothing_without_alerts(output: dict[str, Any]) -> None:
    """A "0 alerts" entry supports no claim and costs context on every later turn."""
    evidence: dict[str, Any] = {}

    _map_alertmanager_alerts(evidence, output, {})

    assert evidence == {}


@pytest.mark.parametrize(
    "output",
    [
        pytest.param({}, id="empty-payload"),
        pytest.param(
            tool_unavailable("alertmanager", "connection refused", silences=[], total=0),
            id="unavailable-envelope",
        ),
        pytest.param(
            {"source": "alertmanager_silences", "available": True, "silences": []},
            id="no-silences",
        ),
    ],
)
def test_silences_mapper_records_nothing_without_silences(output: dict[str, Any]) -> None:
    evidence: dict[str, Any] = {}

    _map_alertmanager_silences(evidence, output, {})

    assert evidence == {}


def test_alerts_mapper_states_zero_firing_rather_than_omitting_it() -> None:
    """ "3 alerts, 0 firing" is a finding; a bare count hides whether any fired."""
    # Arrange
    evidence: dict[str, Any] = {}
    output = {
        "source": "alertmanager",
        "available": True,
        "alerts": [{"status": "suppressed"}, {"status": "suppressed"}],
        "firing_alerts": [],
        "total": 2,
    }

    # Act
    _map_alertmanager_alerts(evidence, output, {})

    # Assert
    assert evidence["catalog_entries"][0]["summary"] == "2 alerts, 0 firing"


def test_silences_mapper_states_zero_active_rather_than_omitting_it() -> None:
    # Arrange
    evidence: dict[str, Any] = {}
    output = {
        "source": "alertmanager_silences",
        "available": True,
        "silences": [{"status": {"state": "expired"}}],
        "active_silences": [],
        "total": 1,
    }

    # Act
    _map_alertmanager_silences(evidence, output, {})

    # Assert
    assert evidence["catalog_entries"][0]["summary"] == "1 silences, 0 active"

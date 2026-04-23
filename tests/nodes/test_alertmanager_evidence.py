from __future__ import annotations

from types import SimpleNamespace

from app.nodes.investigate.processing.post_process import (
    _map_alertmanager_alerts,
    _map_alertmanager_silences,
    build_evidence_summary,
    merge_evidence,
)

# ---------------------------------------------------------------------------
# _map_alertmanager_alerts
# ---------------------------------------------------------------------------


def test_map_alertmanager_alerts_with_data() -> None:
    data = {
        "source": "alertmanager_alerts",
        "available": True,
        "alerts": [
            {"fingerprint": "abc", "status": "active", "labels": {"alertname": "HighErrorRate"}},
        ],
        "firing_alerts": [
            {"fingerprint": "abc", "status": "active", "labels": {"alertname": "HighErrorRate"}},
        ],
        "total": 1,
    }
    result = _map_alertmanager_alerts(data)
    assert result["alertmanager_alerts"] == data["alerts"]
    assert result["alertmanager_firing_alerts"] == data["firing_alerts"]
    assert result["alertmanager_alerts_total"] == 1


def test_map_alertmanager_alerts_empty() -> None:
    data = {"alerts": [], "firing_alerts": [], "total": 0}
    result = _map_alertmanager_alerts(data)
    assert result["alertmanager_alerts"] == []
    assert result["alertmanager_firing_alerts"] == []
    assert result["alertmanager_alerts_total"] == 0


def test_map_alertmanager_alerts_missing_keys() -> None:
    result = _map_alertmanager_alerts({})
    assert result["alertmanager_alerts"] == []
    assert result["alertmanager_firing_alerts"] == []
    assert result["alertmanager_alerts_total"] == 0


def test_map_alertmanager_alerts_none_values() -> None:
    data = {"alerts": None, "firing_alerts": None, "total": None}
    result = _map_alertmanager_alerts(data)
    assert result["alertmanager_alerts"] == []
    assert result["alertmanager_firing_alerts"] == []
    assert result["alertmanager_alerts_total"] == 0


# ---------------------------------------------------------------------------
# _map_alertmanager_silences
# ---------------------------------------------------------------------------


def test_map_alertmanager_silences_with_data() -> None:
    silence = {
        "id": "s1",
        "status": "active",
        "matchers": [{"name": "alertname", "value": "HighErrorRate"}],
        "comment": "planned maintenance",
        "created_by": "fellix",
        "starts_at": "2026-04-15T00:00:00Z",
        "ends_at": "2026-04-15T23:59:00Z",
    }
    data = {
        "source": "alertmanager_silences",
        "available": True,
        "silences": [silence],
        "active_silences": [silence],
        "total": 1,
    }
    result = _map_alertmanager_silences(data)
    assert result["alertmanager_silences"] == [silence]
    assert result["alertmanager_active_silences"] == [silence]
    assert result["alertmanager_silences_total"] == 1


def test_map_alertmanager_silences_empty() -> None:
    data = {"silences": [], "active_silences": [], "total": 0}
    result = _map_alertmanager_silences(data)
    assert result["alertmanager_silences"] == []
    assert result["alertmanager_active_silences"] == []
    assert result["alertmanager_silences_total"] == 0


def test_map_alertmanager_silences_missing_keys() -> None:
    result = _map_alertmanager_silences({})
    assert result["alertmanager_silences"] == []
    assert result["alertmanager_active_silences"] == []
    assert result["alertmanager_silences_total"] == 0


def test_map_alertmanager_silences_none_values() -> None:
    data = {"silences": None, "active_silences": None, "total": None}
    result = _map_alertmanager_silences(data)
    assert result["alertmanager_silences"] == []
    assert result["alertmanager_active_silences"] == []
    assert result["alertmanager_silences_total"] == 0


# ---------------------------------------------------------------------------
# build_evidence_summary — alertmanager_alerts
# ---------------------------------------------------------------------------


def test_build_evidence_summary_alertmanager_alerts_with_firing() -> None:
    execution_results = {
        "alertmanager_alerts": SimpleNamespace(
            success=True,
            data={
                "alerts": [{"fingerprint": "abc"}],
                "firing_alerts": [{"fingerprint": "abc"}],
                "total": 1,
            },
        )
    }
    summary = build_evidence_summary(execution_results)
    assert summary == "alertmanager:1 alerts (1 firing)"


def test_build_evidence_summary_alertmanager_alerts_none_firing() -> None:
    execution_results = {
        "alertmanager_alerts": SimpleNamespace(
            success=True,
            data={"alerts": [], "firing_alerts": None, "total": 0},
        )
    }
    summary = build_evidence_summary(execution_results)
    assert summary == "alertmanager:0 alerts (0 firing)"


def test_build_evidence_summary_alertmanager_alerts_empty() -> None:
    execution_results = {
        "alertmanager_alerts": SimpleNamespace(
            success=True,
            data={"alerts": [], "firing_alerts": [], "total": 0},
        )
    }
    summary = build_evidence_summary(execution_results)
    assert summary == "alertmanager:0 alerts (0 firing)"


# ---------------------------------------------------------------------------
# build_evidence_summary — alertmanager_silences
# ---------------------------------------------------------------------------


def test_build_evidence_summary_alertmanager_silences_with_active() -> None:
    execution_results = {
        "alertmanager_silences": SimpleNamespace(
            success=True,
            data={"silences": [{"id": "s1"}], "active_silences": [{"id": "s1"}], "total": 1},
        )
    }
    summary = build_evidence_summary(execution_results)
    assert summary == "alertmanager:1 silences (1 active)"


def test_build_evidence_summary_alertmanager_silences_none_active() -> None:
    execution_results = {
        "alertmanager_silences": SimpleNamespace(
            success=True,
            data={"silences": [], "active_silences": None, "total": 0},
        )
    }
    summary = build_evidence_summary(execution_results)
    assert summary == "alertmanager:0 silences (0 active)"


def test_build_evidence_summary_alertmanager_silences_empty() -> None:
    execution_results = {
        "alertmanager_silences": SimpleNamespace(
            success=True,
            data={"silences": [], "active_silences": [], "total": 0},
        )
    }
    summary = build_evidence_summary(execution_results)
    assert summary == "alertmanager:0 silences (0 active)"


# ---------------------------------------------------------------------------
# merge_evidence integration — both tools together
# ---------------------------------------------------------------------------


def test_merge_evidence_maps_alertmanager_results() -> None:
    alert = {"fingerprint": "abc", "status": "active", "labels": {"alertname": "HighErrorRate"}}
    silence = {"id": "s1", "status": "active", "matchers": []}
    execution_results = {
        "alertmanager_alerts": SimpleNamespace(
            success=True,
            data={"alerts": [alert], "firing_alerts": [alert], "total": 1},
        ),
        "alertmanager_silences": SimpleNamespace(
            success=True,
            data={"silences": [silence], "active_silences": [silence], "total": 1},
        ),
    }
    evidence = merge_evidence({}, execution_results)
    assert evidence["alertmanager_alerts"] == [alert]
    assert evidence["alertmanager_firing_alerts"] == [alert]
    assert evidence["alertmanager_alerts_total"] == 1
    assert evidence["alertmanager_silences"] == [silence]
    assert evidence["alertmanager_active_silences"] == [silence]
    assert evidence["alertmanager_silences_total"] == 1

    summary = build_evidence_summary(execution_results)
    assert "alertmanager:1 alerts (1 firing)" in summary
    assert "alertmanager:1 silences (1 active)" in summary

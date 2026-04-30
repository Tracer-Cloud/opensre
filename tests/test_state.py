from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.state import AgentStateModel, make_chat_state, make_initial_state


def test_make_initial_state_validates_and_sets_defaults() -> None:
    state = make_initial_state(
        alert_name="HighErrorRate",
        pipeline_name="payments",
        severity="critical",
        raw_alert={"source": "grafana"},
    )

    assert state["mode"] == "investigation"
    raw_alert = state["raw_alert"]
    assert isinstance(raw_alert, dict)
    assert raw_alert["source"] == "grafana"
    assert raw_alert["commonLabels"] == {}
    assert raw_alert["commonAnnotations"] == {}
    assert raw_alert["canonical_alert"]["schema"] == "opensre.alert.v1"
    assert state["planned_actions"] == []
    assert state.get("opensre_evaluate") is False


def test_make_initial_state_strips_rubric_when_not_evaluate() -> None:
    raw = {
        "commonAnnotations": {"summary": "x", "scoring_points": "secret rubric"},
        "foo": 1,
    }
    state = make_initial_state(
        "A",
        "p",
        "warning",
        raw_alert=raw,
        opensre_evaluate=False,
    )
    assert not (state.get("opensre_eval_rubric") or "").strip()
    ra = state["raw_alert"]
    assert isinstance(ra, dict)
    assert "scoring_points" not in (ra.get("commonAnnotations") or {})


def test_make_initial_state_evaluate_strips_scoring_points() -> None:
    raw = {
        "commonAnnotations": {"summary": "x", "scoring_points": "rubric text"},
        "foo": 1,
    }
    state = make_initial_state(
        "A",
        "p",
        "warning",
        raw_alert=raw,
        opensre_evaluate=True,
    )
    assert state["opensre_evaluate"] is True
    assert "rubric text" in (state.get("opensre_eval_rubric") or "")
    ra = state["raw_alert"]
    assert isinstance(ra, dict)
    assert "scoring_points" not in (ra.get("commonAnnotations") or {})


def test_make_chat_state_validates_messages() -> None:
    state = make_chat_state(messages=[{"role": "user", "content": "hello"}])

    assert state["mode"] == "chat"
    assert state["messages"][0]["content"] == "hello"


def test_agent_state_model_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="mesages.*messages"):
        AgentStateModel.model_validate({"mode": "chat", "mesages": []})


def test_make_initial_state_normalizes_datadog_tags_and_process_fields() -> None:
    state = make_initial_state(
        alert_name="HighErrorRate",
        pipeline_name="payments",
        severity="critical",
        raw_alert={
            "alert_source": "datadog",
            "alert_name": "Datadog monitor: process crash",
            "tags": "env:prod,service:payments,process_name:python,pid:4242",
            "command_line": "python worker.py --queue payments",
        },
    )

    raw_alert = state["raw_alert"]
    assert isinstance(raw_alert, dict)
    assert raw_alert["commonLabels"]["service"] == "payments"
    assert raw_alert["commonLabels"]["process_name"] == "python"
    assert raw_alert["process_name"] == "python"
    assert raw_alert["cmdline"] == "python worker.py --queue payments"
    assert raw_alert["pid"] == 4242

    canonical = raw_alert["canonical_alert"]
    assert canonical["alert_source"] == "datadog"
    assert canonical["process"]["name"] == "python"
    assert canonical["process"]["cmdline"] == "python worker.py --queue payments"
    assert canonical["process"]["pid"] == 4242


def test_make_initial_state_uses_existing_annotations_and_labels_for_canonical_fields() -> None:
    state = make_initial_state(
        alert_name="ignored",
        pipeline_name="ignored",
        severity="warning",
        raw_alert={
            "title": "[FIRING] CPU high",
            "alert_source": "grafana",
            "commonLabels": {
                "alertname": "CPUHigh",
                "pipeline_name": "payments_etl",
                "severity": "critical",
                "pid": "1001",
            },
            "commonAnnotations": {
                "summary": "CPU high on worker",
                "cmdline": "/usr/bin/python worker.py",
                "process_name": "python",
            },
        },
    )

    raw_alert = state["raw_alert"]
    assert isinstance(raw_alert, dict)
    assert raw_alert["process_name"] == "python"
    assert raw_alert["cmdline"] == "/usr/bin/python worker.py"
    assert raw_alert["pid"] == 1001

    canonical = raw_alert["canonical_alert"]
    assert canonical["alert_name"] == "[FIRING] CPU high"
    assert canonical["pipeline_name"] == "payments_etl"
    assert canonical["severity"] == "critical"


def test_make_initial_state_keeps_common_labels_and_canonical_labels_separate() -> None:
    state = make_initial_state(
        alert_name="HighErrorRate",
        pipeline_name="payments",
        severity="critical",
        raw_alert={
            "commonLabels": {"alertname": "CPUHigh"},
            "commonAnnotations": {"summary": "CPU high"},
        },
    )

    raw_alert = state["raw_alert"]
    assert isinstance(raw_alert, dict)
    canonical = raw_alert["canonical_alert"]

    canonical["labels"]["mutated"] = "yes"
    canonical["annotations"]["extra"] = "note"

    assert raw_alert["commonLabels"] == {"alertname": "CPUHigh"}
    assert raw_alert["commonAnnotations"] == {"summary": "CPU high"}
    assert "mutated" not in raw_alert["commonLabels"]
    assert "extra" not in raw_alert["commonAnnotations"]


def test_make_initial_state_preserves_explicit_empty_common_labels() -> None:
    state = make_initial_state(
        alert_name="HighErrorRate",
        pipeline_name="payments",
        severity="critical",
        raw_alert={
            "commonLabels": {},
            "labels": {"severity": "low", "pipeline": "wrong"},
            "commonAnnotations": {},
            "annotations": {"summary": "should not win"},
        },
    )

    raw_alert = state["raw_alert"]
    assert isinstance(raw_alert, dict)
    assert raw_alert["commonLabels"] == {}
    assert raw_alert["commonAnnotations"] == {}

    canonical = raw_alert["canonical_alert"]
    assert canonical["labels"] == {}
    assert canonical["annotations"] == {}


def test_make_initial_state_preserves_float_pid() -> None:
    state = make_initial_state(
        alert_name="HighErrorRate",
        pipeline_name="payments",
        severity="critical",
        raw_alert={
            "commonLabels": {"pid": 4242.0},
            "commonAnnotations": {"process_name": "worker"},
        },
    )

    raw_alert = state["raw_alert"]
    assert isinstance(raw_alert, dict)
    assert raw_alert["pid"] == 4242

    canonical = raw_alert["canonical_alert"]
    assert canonical["process"]["pid"] == 4242

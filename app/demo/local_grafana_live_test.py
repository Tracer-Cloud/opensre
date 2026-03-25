from __future__ import annotations

from app.demo.local_grafana_live import (
    DEMO_TIME_RANGE_MINUTES,
    LOCAL_GRAFANA_URL,
    build_synthetic_alert,
    prepare_demo_state,
)
from app.demo.local_grafana_seed import DEMO_CORRELATION_ID, DEMO_RUN_ID


def test_build_synthetic_alert_points_to_local_grafana() -> None:
    alert = build_synthetic_alert()

    assert alert["externalURL"] == LOCAL_GRAFANA_URL
    assert alert["commonLabels"]["pipeline_name"] == "events_fact"
    assert alert["commonAnnotations"]["execution_run_id"] == DEMO_RUN_ID
    assert alert["commonAnnotations"]["correlation_id"] == DEMO_CORRELATION_ID


def test_prepare_demo_state_sets_live_grafana_endpoint() -> None:
    evidence = {
        "grafana_logs": [{"message": "demo log"}],
        "grafana_error_logs": [{"message": "demo error"}],
        "grafana_logs_query": '{service_name="prefect-etl-pipeline-local"}',
        "grafana_logs_service": "prefect-etl-pipeline-local",
        "grafana_loki_datasource_uid": "local-loki",
    }

    state = prepare_demo_state(evidence)

    assert state["alert_source"] == "grafana"
    assert state["available_sources"]["grafana"]["grafana_endpoint"] == LOCAL_GRAFANA_URL
    assert state["available_sources"]["grafana"]["connection_verified"] is True
    assert state["available_sources"]["grafana"]["execution_run_id"] == DEMO_RUN_ID
    assert state["available_sources"]["grafana"]["time_range_minutes"] == DEMO_TIME_RANGE_MINUTES
    assert state["evidence"]["grafana_logs_service"] == "prefect-etl-pipeline-local"

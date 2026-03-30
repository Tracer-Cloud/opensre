from __future__ import annotations

from app.demo.local_grafana_seed import (
    DEMO_CORRELATION_ID,
    DEMO_RUN_ID,
    PIPELINE_NAME,
    SERVICE_NAME,
    build_log_streams,
)


def test_build_log_streams_include_pipeline_and_supporting_telemetry() -> None:
    streams = build_log_streams(1_000_000_000_000)

    assert len(streams) == 2
    assert {stream["stream"]["stream_kind"] for stream in streams} == {"pipeline", "supporting"}

    for stream in streams:
        labels = stream["stream"]
        assert labels["service_name"] == SERVICE_NAME
        assert labels["pipeline_name"] == PIPELINE_NAME
        assert labels["execution_run_id"] == DEMO_RUN_ID

    messages = [message for stream in streams for _ts, message in stream["values"]]
    assert any(DEMO_RUN_ID in message for message in messages)
    assert any(DEMO_CORRELATION_ID in message for message in messages)
    assert any("telemetry_source" in message for message in messages)

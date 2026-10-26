"""Tests for the local Grafana Tempo trace seed."""

from tests.e2e.tempo.local_seed import TRACE_ID, _trace_payload


def test_trace_payload_contains_searchable_error_span() -> None:
    end_time = 3_000_000_000

    payload = _trace_payload(end_time)

    resource_span = payload["resourceSpans"][0]
    resource_attributes = resource_span["resource"]["attributes"]
    span = resource_span["scopeSpans"][0]["spans"][0]
    assert resource_attributes[0]["value"]["stringValue"] == "checkout-service"
    assert span["traceId"] == TRACE_ID
    assert span["name"] == "POST /checkout"
    assert int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"]) == 2_000_000_000
    assert span["status"]["code"] == 2
    http_status = next(
        attribute["value"]["intValue"]
        for attribute in span["attributes"]
        if attribute["key"] == "http.response.status_code"
    )
    assert http_status == "500"

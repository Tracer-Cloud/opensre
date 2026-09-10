"""Unit tests for the shared OTLP/JSON trace parser."""

from __future__ import annotations

from infrastructure.observability.otlp_parser import extract_span_attributes, parse_otlp_trace


def test_extract_span_attributes_value_kinds() -> None:
    span = {
        "attributes": [
            {"key": "str", "value": {"stringValue": "v"}},
            {"key": "int", "value": {"intValue": "42"}},
            {"key": "bool", "value": {"boolValue": True}},
            {"key": "double", "value": {"doubleValue": 1.5}},
            {"key": "empty", "value": {}},
            {"value": {"stringValue": "no-key"}},
        ]
    }
    attrs = extract_span_attributes(span)
    assert attrs == {"str": "v", "int": "42", "bool": True, "double": 1.5}


def test_parse_otlp_trace_flattens_spans_with_service_name() -> None:
    trace = {
        "batches": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "checkout"}}]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "name": "POST /checkout",
                                "spanId": "span-1",
                                "parentSpanId": "span-0",
                                "traceId": "trace-1",
                                "kind": 2,
                                "startTimeUnixNano": "1000000000",
                                "endTimeUnixNano": "1150000000",
                                "status": {"code": 2, "message": "boom"},
                                "attributes": [
                                    {
                                        "key": "http.status_code",
                                        "value": {"intValue": "500"},
                                    }
                                ],
                            }
                        ]
                    }
                ],
            }
        ]
    }
    spans = parse_otlp_trace(trace)
    assert len(spans) == 1
    span = spans[0]
    assert span["name"] == "POST /checkout"
    assert span["service_name"] == "checkout"
    assert span["span_id"] == "span-1"
    assert span["parent_span_id"] == "span-0"
    assert span["duration_ms"] == 150.0
    assert span["status_code"] == 2
    assert span["status_message"] == "boom"
    assert span["attributes"]["http.status_code"] == "500"


def test_parse_otlp_trace_handles_empty_and_malformed() -> None:
    assert parse_otlp_trace({}) == []
    assert parse_otlp_trace({"batches": ["not-a-dict"]}) == []
    assert parse_otlp_trace({"batches": [{"scopeSpans": [{"spans": []}]}]}) == []


def test_extract_span_attributes_tolerates_null_and_non_dict_entries() -> None:
    assert extract_span_attributes({"attributes": None}) == {}
    assert extract_span_attributes(None) == {}
    span = {
        "attributes": [
            None,
            "junk",
            {"key": "null-value", "value": None},
            {"key": "ok", "value": {"stringValue": "v"}},
        ]
    }
    assert extract_span_attributes(span) == {"ok": "v"}


def test_parse_otlp_trace_tolerates_null_collections() -> None:
    assert parse_otlp_trace({"batches": None}) == []

    trace = {
        "batches": [
            {
                "resource": None,
                "scopeSpans": [
                    {
                        "spans": [
                            {"name": "kept", "attributes": None, "status": None},
                        ]
                    }
                ],
            },
            {"resource": {"attributes": None}, "scopeSpans": None},
        ]
    }
    spans = parse_otlp_trace(trace)
    assert [span["name"] for span in spans] == ["kept"]
    assert spans[0]["attributes"] == {}
    assert spans[0]["status_code"] == ""

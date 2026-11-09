"""Shared parsing for OTLP/JSON trace payloads.

Used by any client that fetches a single trace in OpenTelemetry JSON form
(``{"batches": [{"resource": ..., "scopeSpans": [{"spans": [...]}]}]}``):
the standalone Tempo client and the Grafana Cloud Tempo mixin both consume it.
"""

from __future__ import annotations

from typing import Any

# OTLP/JSON scalar value kinds, in the order an attribute's single-key value dict
# may carry them (int64 arrives as a string per the OTLP spec — kept as-is).
_OTLP_SCALAR_KINDS = ("stringValue", "intValue", "boolValue", "doubleValue")

#: Nanoseconds → milliseconds for OTLP span duration.
_NANOSECONDS_PER_MILLISECOND = 1_000_000
#: Decimal places kept on parsed OTLP durations (milliseconds).
_OTLP_DURATION_MILLISECONDS_DECIMAL_PLACES = 4
_EMPTY_DURATION_MILLISECONDS = 0.0
_UNKNOWN_SPAN_NAME = "unknown"


def extract_span_attributes(span: dict[str, Any]) -> dict[str, Any]:
    """Flatten an OTLP attribute list into a plain key -> value mapping.

    Handles the common OTLP/JSON scalar value kinds. Attributes without a key or
    with an unsupported value kind are skipped.
    """
    attributes: dict[str, Any] = {}
    for attr in span.get("attributes", []):
        key = attr.get("key", "")
        if not key:
            continue
        value = attr.get("value", {})
        for kind in _OTLP_SCALAR_KINDS:
            if kind in value:
                attributes[key] = value[kind]
                break
    return attributes


def _duration_ms(start_unix_nano: Any, end_unix_nano: Any) -> float:
    """Span duration in milliseconds from OTLP nanosecond timestamps."""
    try:
        start = int(start_unix_nano)
        end = int(end_unix_nano)
    except (TypeError, ValueError):
        return _EMPTY_DURATION_MILLISECONDS
    if end <= start:
        return _EMPTY_DURATION_MILLISECONDS
    return round(
        (end - start) / _NANOSECONDS_PER_MILLISECOND,
        _OTLP_DURATION_MILLISECONDS_DECIMAL_PLACES,
    )


def parse_otlp_trace(trace_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse an OTLP/JSON trace into a flat list of span dicts.

    The ``service_name`` is lifted from each batch's resource attributes so
    callers can correlate spans to services without a nested lookup.
    """
    spans: list[dict[str, Any]] = []

    for batch in trace_data.get("batches", []):
        if not isinstance(batch, dict):
            continue
        resource_attributes = extract_span_attributes(batch.get("resource", {}))
        service_name = str(resource_attributes.get("service.name", ""))

        for scope in batch.get("scopeSpans", []):
            if not isinstance(scope, dict):
                continue
            for span in scope.get("spans", []):
                if not isinstance(span, dict):
                    continue
                status = span.get("status") or {}
                spans.append(
                    {
                        "name": span.get("name", _UNKNOWN_SPAN_NAME),
                        "span_id": span.get("spanId", ""),
                        "parent_span_id": span.get("parentSpanId", ""),
                        "trace_id": span.get("traceId", ""),
                        "kind": span.get("kind", ""),
                        "service_name": service_name,
                        "duration_ms": _duration_ms(
                            span.get("startTimeUnixNano"),
                            span.get("endTimeUnixNano"),
                        ),
                        "status_code": status.get("code", ""),
                        "status_message": status.get("message", ""),
                        "attributes": extract_span_attributes(span),
                    }
                )

    return spans

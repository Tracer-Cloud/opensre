"""Synthetic standard OTLP payloads for the pinned Collector/exporter contract."""

from __future__ import annotations

import uuid
from typing import Any

from core.telemetry.evidence_queries import InvestigationScope


def _attributes(values: dict[str, str]) -> list[dict[str, Any]]:
    return [{"key": key, "value": {"stringValue": value}} for key, value in values.items()]


def payloads(scope: InvestigationScope) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Emit controlled symptoms, correlated logs and explicit historical delta counters."""
    window, baseline = scope.incident, scope.window("baseline")
    identifiers = {name: uuid.uuid4().hex for name in ("auth", "timeout", "dns", "boundary")}
    root_span, child_span = uuid.uuid4().hex[:16], uuid.uuid4().hex[:16]
    identifiers.update(root_span=root_span, child_span=child_span)
    resource = {
        "service.name": window.service,
        "deployment.environment.name": window.environment,
        "service.version": "v42",
    }

    def span(
        trace_id: str,
        message: str,
        timestamp: float,
        *,
        kind: int = 2,
        span_id: str | None = None,
        parent: str | None = None,
        error: bool = True,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "traceId": trace_id,
            "spanId": span_id or uuid.uuid4().hex[:16],
            "name": "Charge",
            "kind": kind,
            "startTimeUnixNano": str(int(timestamp * 1e9)),
            "endTimeUnixNano": str(int((timestamp + 0.01) * 1e9)),
            "status": {"code": 2 if error else 1, "message": message},
            "attributes": _attributes(
                {
                    "peer.service": "provider",
                    "server.address": "provider.invalid",
                    "error.type": "SyntheticFailure",
                }
            ),
        }
        if parent is not None:
            data["parentSpanId"] = parent
        return data

    spans = [
        span(identifiers["auth"], "Invalid token", window.start + 5, span_id=root_span),
        span(
            identifiers["auth"],
            "Token expired",
            window.start + 6,
            kind=3,
            span_id=child_span,
            parent=root_span,
        ),
        span(identifiers["timeout"], "Upstream deadline exceeded", window.start + 7),
        span(identifiers["dns"], "getaddrinfo ENOTFOUND provider.invalid", window.start + 8),
        span(identifiers["boundary"], "excluded_start", window.start, error=False),
        span(identifiers["boundary"], "included_end", window.end, error=False),
        span(identifiers["boundary"], "excluded_after", window.end + 1, error=False),
    ]
    trace_resources = [
        {
            "resource": {"attributes": _attributes(resource)},
            "scopeSpans": [{"scope": {"name": "evidence-contract-fixture"}, "spans": spans}],
        }
    ]
    for override in (
        {"service.name": "other-" + window.service},
        {"deployment.environment.name": "other"},
    ):
        trace_resources.append(
            {
                "resource": {"attributes": _attributes({**resource, **override})},
                "scopeSpans": [
                    {"spans": [span(identifiers["auth"], "OUT_OF_SCOPE", window.start + 5)]}
                ],
            }
        )

    def log(message: str, timestamp: float) -> dict[str, Any]:
        return {
            "timeUnixNano": str(int(timestamp * 1e9)),
            "severityNumber": 17,
            "severityText": "ERROR",
            "traceId": identifiers["auth"],
            "spanId": child_span,
            "body": {"stringValue": message},
        }

    log_resources = [
        {
            "resource": {"attributes": _attributes(resource)},
            "scopeLogs": [
                {
                    "logRecords": [
                        log(
                            "Validator: token expired. password=fixture-secret alice@example.com",
                            window.start + 6,
                        ),
                        log("Details: " + "x" * 450, window.start + 7),
                        log("excluded_start", window.start),
                        log("excluded_after", window.end + 1),
                    ]
                }
            ],
        }
    ]
    for override in (
        {"service.name": "other-" + window.service},
        {"deployment.environment.name": "other"},
    ):
        log_resources.append(
            {
                "resource": {"attributes": _attributes({**resource, **override})},
                "scopeLogs": [{"logRecords": [log("OUT_OF_SCOPE", window.start + 6)]}],
            }
        )

    def metric(timestamp: float, errors: bool, count: int) -> dict[str, Any]:
        return {
            "attributes": _attributes(
                {
                    "status.code": "STATUS_CODE_ERROR" if errors else "STATUS_CODE_UNSET",
                    "span.kind": "SPAN_KIND_SERVER",
                }
            ),
            "startTimeUnixNano": str(int((timestamp - 5) * 1e9)),
            "timeUnixNano": str(int(timestamp * 1e9)),
            "asDouble": count,
        }

    def metric_resource(attributes: dict[str, str], points: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "resource": {"attributes": _attributes(attributes)},
            "scopeMetrics": [
                {
                    "metrics": [
                        {
                            "name": "traces.span.metrics.calls",
                            "sum": {
                                "aggregationTemporality": 1,
                                "isMonotonic": True,
                                "dataPoints": points,
                            },
                        }
                    ]
                }
            ],
        }

    metric_resources = [
        metric_resource(resource, [metric(baseline.end, False, 30), metric(window.end, True, 30)])
    ]
    for override in (
        {"service.name": "other-" + window.service},
        {"deployment.environment.name": "other"},
    ):
        metric_resources.append(
            metric_resource({**resource, **override}, [metric(window.end, True, 999)])
        )
    return {
        "traces": {"resourceSpans": trace_resources},
        "logs": {"resourceLogs": log_resources},
        "metrics": {"resourceMetrics": metric_resources},
    }, identifiers

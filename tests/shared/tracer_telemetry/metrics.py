"""
OpenTelemetry metrics for pipeline observability.

Metrics follow OpenTelemetry semantic conventions:
- Counter names use dot notation and describe what is counted
- Histogram names describe what is measured with unit suffix
- Units follow UCUM conventions (s for seconds, 1 for unitless counts)

These metrics are separate from tracing - they measure aggregate pipeline
behavior, not individual request flows.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader


@dataclass(frozen=True)
class PipelineMetrics:
    """
    Container for pipeline-related OpenTelemetry metrics.

    Metrics are created via proper OpenTelemetry meter APIs - counters for
    things that only go up, histograms for distributions. Each metric has
    semantic naming that describes what it measures.
    """
    runs_total: metrics.Counter
    runs_failed_total: metrics.Counter
    duration_seconds: metrics.Histogram
    records_processed_total: metrics.Counter
    records_failed_total: metrics.Counter

    @classmethod
    def create(cls, meter: metrics.Meter) -> PipelineMetrics:
        """Create metrics instruments using standard OpenTelemetry APIs."""
        return cls(
            runs_total=meter.create_counter(
                "pipeline.runs",
                description="Total number of pipeline executions",
                unit="{run}",
            ),
            runs_failed_total=meter.create_counter(
                "pipeline.runs.failed",
                description="Total number of failed pipeline executions",
                unit="{run}",
            ),
            duration_seconds=meter.create_histogram(
                "pipeline.duration",
                description="Duration of pipeline execution",
                unit="s",
            ),
            records_processed_total=meter.create_counter(
                "pipeline.records.processed",
                description="Total number of records successfully processed",
                unit="{record}",
            ),
            records_failed_total=meter.create_counter(
                "pipeline.records.failed",
                description="Total number of records that failed processing",
                unit="{record}",
            ),
        )

    @classmethod
    def noop(cls) -> PipelineMetrics:
        """Create no-op metrics for when OTLP export is unavailable."""
        meter = metrics.get_meter("tracer_telemetry.noop")
        return cls.create(meter)


def _parse_headers() -> dict[str, str]:
    headers_str = os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")
    headers: dict[str, str] = {}
    if headers_str:
        for pair in headers_str.split(","):
            if "=" in pair:
                key, value = pair.split("=", 1)
                headers[key.strip()] = value.strip()
    return headers


def _get_metric_exporter():
    protocol = os.getenv(
        "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
        os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc"),
    )
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT") or os.getenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    headers = _parse_headers()

    if protocol in ("http/protobuf", "http"):
        try:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )

            metrics_endpoint = endpoint
            if endpoint and not endpoint.endswith("/v1/metrics"):
                metrics_endpoint = endpoint.rstrip("/") + "/v1/metrics"
            return (
                OTLPMetricExporter(endpoint=metrics_endpoint, headers=headers)
                if metrics_endpoint
                else OTLPMetricExporter(headers=headers)
            )
        except ImportError:
            pass

    try:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )

        return OTLPMetricExporter()
    except ImportError:
        return None


def setup_metrics(resource) -> PipelineMetrics:
    exporter = _get_metric_exporter()
    if exporter is None:
        logging.getLogger(__name__).warning("OTLP metric exporter is unavailable")
        return PipelineMetrics.noop()

    metric_reader = PeriodicExportingMetricReader(exporter)
    provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(provider)
    logging.getLogger(__name__).info(
        json.dumps(
            {
                "event": "otel_metrics_configured",
                "protocol": os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc"),
                "endpoint": os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
                "exporter": exporter.__class__.__name__,
            }
        )
    )
    meter = metrics.get_meter("tracer_telemetry")
    return PipelineMetrics.create(meter)

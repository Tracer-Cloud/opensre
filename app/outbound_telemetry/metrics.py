"""
OpenTelemetry metrics for pipeline observability.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

from app.outbound_telemetry.env import parse_otel_headers


@dataclass(frozen=True)
class PipelineMetrics:
    runs_total: metrics.Counter
    runs_failed_total: metrics.Counter
    duration_seconds: metrics.Histogram
    records_processed_total: metrics.Counter
    records_failed_total: metrics.Counter

    @classmethod
    def create(cls, meter: metrics.Meter) -> PipelineMetrics:
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
        meter = metrics.get_meter("outbound_telemetry.noop")
        return cls.create(meter)


def _get_metric_exporter():
    protocol = os.getenv(
        "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
        os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc"),
    )
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT") or os.getenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    headers = parse_otel_headers()

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
    meter = metrics.get_meter("outbound_telemetry")
    return PipelineMetrics.create(meter)

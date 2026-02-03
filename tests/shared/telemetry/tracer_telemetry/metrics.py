from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

# Try to import gRPC exporter first, fall back to HTTP if not available
try:
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
except ImportError:
    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    except ImportError:
        OTLPMetricExporter = None


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
                "pipeline_runs_total",
                description="Total pipeline runs",
                unit="1",
            ),
            runs_failed_total=meter.create_counter(
                "pipeline_runs_failed_total",
                description="Total failed pipeline runs",
                unit="1",
            ),
            duration_seconds=meter.create_histogram(
                "pipeline_duration_seconds",
                description="Pipeline duration in seconds",
                unit="s",
            ),
            records_processed_total=meter.create_counter(
                "records_processed_total",
                description="Total records processed",
                unit="1",
            ),
            records_failed_total=meter.create_counter(
                "records_failed_total",
                description="Total records failed validation",
                unit="1",
            ),
        )

    @classmethod
    def noop(cls) -> PipelineMetrics:
        meter = metrics.get_meter("tracer_telemetry.noop")
        return cls.create(meter)


def setup_metrics(resource) -> PipelineMetrics:
    if OTLPMetricExporter is None:
        logging.getLogger(__name__).warning("OTLP metric exporter is unavailable")
        return PipelineMetrics.noop()

    metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(provider)
    logging.getLogger(__name__).info(
        json.dumps(
            {
                "event": "otel_metrics_configured",
                "protocol": os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc"),
                "endpoint": os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
                "exporter": OTLPMetricExporter.__name__,
            }
        )
    )
    meter = metrics.get_meter("tracer_telemetry")
    return PipelineMetrics.create(meter)

"""
Tracer Telemetry - OpenTelemetry instrumentation for data pipelines.

This module provides standardized telemetry for pipeline observability following
OpenTelemetry best practices:

Architecture:
    - Tracing: Instrument domain logic and adapters, not orchestration layer
    - Metrics: Proper counters/histograms for aggregate pipeline measurements
    - Context: Automatic propagation via OpenTelemetry - no manual threading
    - Errors: Exceptions recorded on spans with proper error status

Prefect Integration:
    Prefect 3.x handles task/flow orchestration visibility through its own UI and
    metrics. Our instrumentation complements this by providing:
    - Domain-level spans (validation, transformation) for business logic visibility
    - Adapter-level spans (S3 operations) with AWS semantic conventions
    - Auto-instrumentation of boto3/requests via OpenTelemetry instrumentors

    We do NOT duplicate Prefect's task-level instrumentation. The span hierarchy is:
    1. Prefect manages flow/task execution boundaries (via Prefect UI)
    2. We instrument what happens inside tasks (domain logic, I/O operations)

Semantic Conventions:
    - AWS S3: aws.s3.bucket, aws.s3.key, aws.s3.operation
    - Domain: data.record.count, data.validation.status, data.transform.status
    - Pipeline: pipeline.name, pipeline.framework

Usage:
    from tracer_telemetry import init_telemetry, get_tracer, traced_operation

    telemetry = init_telemetry(service_name="my-pipeline")

    # Domain code uses spans with error handling
    with traced_operation(tracer, "process_data", {"count": 10}) as span:
        result = do_work()
        span.set_attribute("output.count", len(result))
"""

from __future__ import annotations

import importlib
import json
import os
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor

from tracer_telemetry.config import apply_otel_env_defaults, build_resource
from tracer_telemetry.logging import setup_logging
from tracer_telemetry.metrics import PipelineMetrics, setup_metrics
from tracer_telemetry.tracing import setup_tracing, traced_operation

__all__ = [
    "init_telemetry",
    "get_tracer",
    "get_metrics",
    "traced_operation",
    "PipelineTelemetry",
    "PipelineMetrics",
]

_telemetry: PipelineTelemetry | None = None
_std_logging = importlib.import_module("logging")


@dataclass(frozen=True)
class PipelineTelemetry:
    tracer: trace.Tracer
    metrics: PipelineMetrics

    def record_run(
        self,
        *,
        status: str,
        duration_seconds: float | None,
        record_count: int = 0,
        failure_count: int = 0,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        attributes = attributes or {}
        metric_attrs = {"status": status, **attributes}

        self.metrics.runs_total.add(1, metric_attrs)
        if status != "success":
            self.metrics.runs_failed_total.add(1, metric_attrs)
        if duration_seconds is not None:
            self.metrics.duration_seconds.record(duration_seconds, metric_attrs)
        if record_count:
            self.metrics.records_processed_total.add(record_count, metric_attrs)
        if failure_count:
            self.metrics.records_failed_total.add(failure_count, metric_attrs)

    def flush(self) -> None:
        """Force flush all telemetry data (critical for Lambda/short-lived processes)."""
        try:
            # Flush traces
            provider = trace.get_tracer_provider()
            if hasattr(provider, "force_flush"):
                provider.force_flush(timeout_millis=5000)
        except Exception:
            pass

        try:
            # Flush logs
            from opentelemetry import _logs
            log_provider = _logs.get_logger_provider()
            if hasattr(log_provider, "force_flush"):
                log_provider.force_flush(timeout_millis=5000)
        except Exception:
            pass

        try:
            # Flush metrics
            from opentelemetry import metrics
            meter_provider = metrics.get_meter_provider()
            if hasattr(meter_provider, "force_flush"):
                meter_provider.force_flush(timeout_millis=5000)
        except Exception:
            pass


def init_telemetry(
    *,
    service_name: str,
    resource_attributes: dict[str, Any] | None = None,
) -> PipelineTelemetry:
    global _telemetry
    if _telemetry is not None:
        return _telemetry

    try:
        apply_otel_env_defaults()
        from tracer_telemetry.config import validate_grafana_cloud_config

        config_ok = validate_grafana_cloud_config()
        resource = build_resource(service_name, resource_attributes)
        setup_logging(resource)
        _std_logging.getLogger("tracer_telemetry").setLevel(_std_logging.INFO)
        _std_logging.getLogger("tracer_telemetry").info(
            json.dumps(
                {
                    "event": "otel_env_config",
                    "config_valid": config_ok,
                    "service_name": service_name,
                    "endpoint": os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
                    "protocol": os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", ""),
                }
            )
        )
        tracer = setup_tracing(resource)
        metrics = setup_metrics(resource)

        BotocoreInstrumentor().instrument()
        RequestsInstrumentor().instrument()
        try:
            from opentelemetry.instrumentation.aws_lambda import AwsLambdaInstrumentor
        except ImportError:
            AwsLambdaInstrumentor = None
        if os.getenv("AWS_LAMBDA_FUNCTION_NAME") and AwsLambdaInstrumentor:
            AwsLambdaInstrumentor().instrument()

        _telemetry = PipelineTelemetry(tracer=tracer, metrics=metrics)
    except Exception as exc:  # noqa: BLE001 - avoid breaking pipelines on telemetry failures
        _std_logging.getLogger(__name__).warning("Telemetry init failed: %s", exc)
        _telemetry = PipelineTelemetry(
            tracer=trace.get_tracer(__name__), metrics=PipelineMetrics.noop()
        )

    return _telemetry


def get_tracer(name: str | None = None) -> trace.Tracer:
    if _telemetry is not None:
        return _telemetry.tracer
    return trace.get_tracer(name or __name__)


def get_metrics() -> PipelineMetrics:
    if _telemetry is not None:
        return _telemetry.metrics
    return PipelineMetrics.noop()

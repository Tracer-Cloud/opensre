from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor

from tracer_telemetry.config import apply_otel_env_defaults, build_resource
from tracer_telemetry.logging import setup_logging
from tracer_telemetry.metrics import PipelineMetrics, setup_metrics
from tracer_telemetry.tracing import setup_tracing

_telemetry: "PipelineTelemetry | None" = None


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
        resource = build_resource(service_name, resource_attributes)
        tracer = setup_tracing(resource)
        metrics = setup_metrics(resource)
        setup_logging(resource)

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
        logging.getLogger(__name__).warning("Telemetry init failed: %s", exc)
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

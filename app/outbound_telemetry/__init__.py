"""
Outbound Telemetry - OpenTelemetry instrumentation helpers.

Usage:
    from app.outbound_telemetry import init_telemetry, get_tracer, traced_operation

    telemetry = init_telemetry(service_name="my-pipeline")
"""

from __future__ import annotations

import importlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor

from app.outbound_telemetry.grafana_client import GrafanaCloudClient, GrafanaCloudConfig
from app.outbound_telemetry.logging import setup_logging
from app.outbound_telemetry.metrics import PipelineMetrics, setup_metrics
from app.outbound_telemetry.tracing import setup_tracing, traced_operation
from config.grafana_config import (
    apply_otel_env_defaults,
    build_resource,
    get_aws_lambda_function_name,
    get_otel_exporter_otlp_endpoint,
    get_otel_exporter_otlp_headers,
    get_otel_exporter_otlp_protocol,
    validate_grafana_cloud_config,
)

__all__ = [
    "init_telemetry",
    "get_tracer",
    "get_metrics",
    "traced_operation",
    "PipelineTelemetry",
    "PipelineMetrics",
    "GrafanaCloudClient",
    "GrafanaCloudConfig",
]

_telemetry: PipelineTelemetry | None = None
_std_logging = importlib.import_module("logging")
_DEBUG_LOG_PATH = "/Users/janvincentfranciszek/tracer-agent-2026/.cursor/debug.log"
_DEBUG_SESSION_ID = "debug-session"
_DEBUG_RUN_ID = "pre-fix"


def _debug_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    payload = {
        "sessionId": _DEBUG_SESSION_ID,
        "runId": _DEBUG_RUN_ID,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
    except Exception:
        return


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
            provider = trace.get_tracer_provider()
            if hasattr(provider, "force_flush"):
                provider.force_flush(timeout_millis=5000)
        except Exception:
            pass

        try:
            from opentelemetry import _logs

            log_provider = _logs.get_logger_provider()
            if hasattr(log_provider, "force_flush"):
                log_provider.force_flush(timeout_millis=5000)
        except Exception:
            pass

        try:
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
        # region agent log
        _debug_log(
            "H1",
            "app/outbound_telemetry/__init__.py:init_telemetry",
            "init_start",
            {
                "service_name": service_name,
                "resource_attr_keys": sorted((resource_attributes or {}).keys()),
                "env_present": {
                    "OTEL_EXPORTER_OTLP_ENDPOINT": bool(
                        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
                    ),
                    "OTEL_EXPORTER_OTLP_HEADERS": bool(
                        os.getenv("OTEL_EXPORTER_OTLP_HEADERS")
                    ),
                    "OTEL_EXPORTER_OTLP_PROTOCOL": bool(
                        os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL")
                    ),
                    "GCLOUD_OTLP_ENDPOINT": bool(os.getenv("GCLOUD_OTLP_ENDPOINT")),
                    "GCLOUD_OTLP_AUTH_HEADER": bool(
                        os.getenv("GCLOUD_OTLP_AUTH_HEADER")
                    ),
                    "GCLOUD_HOSTED_LOGS_ID": bool(
                        os.getenv("GCLOUD_HOSTED_LOGS_ID")
                    ),
                    "GCLOUD_HOSTED_LOGS_URL": bool(
                        os.getenv("GCLOUD_HOSTED_LOGS_URL")
                    ),
                },
            },
        )
        # endregion agent log

        apply_otel_env_defaults()
        config_ok = validate_grafana_cloud_config()
        resource = build_resource(service_name, resource_attributes)
        setup_logging(resource)
        _std_logging.getLogger("outbound_telemetry").setLevel(_std_logging.INFO)
        _std_logging.getLogger("outbound_telemetry").info(
            json.dumps(
                {
                    "event": "otel_env_config",
                    "config_valid": config_ok,
                    "service_name": service_name,
                    "endpoint": get_otel_exporter_otlp_endpoint(),
                    "protocol": get_otel_exporter_otlp_protocol(default=""),
                }
            )
        )
        # region agent log
        _debug_log(
            "H2",
            "app/outbound_telemetry/__init__.py:init_telemetry",
            "otel_env_resolved",
            {
                "config_ok": config_ok,
                "endpoint": get_otel_exporter_otlp_endpoint(),
                "protocol": get_otel_exporter_otlp_protocol(default=""),
                "headers_present": bool(get_otel_exporter_otlp_headers()),
            },
        )
        # endregion agent log
        tracer = setup_tracing(resource)
        metrics = setup_metrics(resource)

        BotocoreInstrumentor().instrument()
        RequestsInstrumentor().instrument()
        try:
            from opentelemetry.instrumentation.aws_lambda import (  # type: ignore[import-not-found]
                AwsLambdaInstrumentor,
            )
        except ImportError:
            AwsLambdaInstrumentor = None
        if get_aws_lambda_function_name() and AwsLambdaInstrumentor:
            AwsLambdaInstrumentor().instrument()

        _telemetry = PipelineTelemetry(tracer=tracer, metrics=metrics)
    except Exception as exc:  # noqa: BLE001 - avoid breaking pipelines on telemetry failures
        # region agent log
        _debug_log(
            "H5",
            "app/outbound_telemetry/__init__.py:init_telemetry",
            "init_failed",
            {"error_type": type(exc).__name__, "error_message": str(exc)},
        )
        # endregion agent log
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

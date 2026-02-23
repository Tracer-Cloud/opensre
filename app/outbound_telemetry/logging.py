from __future__ import annotations

import json
import logging
import sys

from opentelemetry import trace

from config.grafana_config import (
    get_effective_otlp_endpoint,
    get_otel_exporter_otlp_endpoint,
    get_otel_exporter_otlp_protocol,
    parse_otel_headers,
)


def _get_log_exporter():
    """Get the appropriate log exporter based on OTEL_EXPORTER_OTLP_PROTOCOL."""
    protocol = get_otel_exporter_otlp_protocol()
    endpoint = get_otel_exporter_otlp_endpoint()
    headers = parse_otel_headers()

    # Use HTTP for http/protobuf protocol (required for Grafana Cloud)
    if protocol in ("http/protobuf", "http"):
        try:
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
            if endpoint and not endpoint.endswith("/v1/logs"):
                logs_endpoint = endpoint.rstrip("/") + "/v1/logs"
            else:
                logs_endpoint = endpoint
            return (
                OTLPLogExporter(endpoint=logs_endpoint, headers=headers)
                if logs_endpoint
                else OTLPLogExporter(headers=headers)
            )
        except ImportError:
            pass

    # Fall back to gRPC
    try:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (  # type: ignore[import-untyped]
            OTLPLogExporter,
        )

        return OTLPLogExporter()
    except ImportError:
        pass

    return None


class ExecutionRunIdLoggingHandler(logging.Handler):
    """Logging handler that injects execution_run_id from active span context into log records."""

    def __init__(self, base_handler: logging.Handler):
        super().__init__()
        self.base_handler = base_handler

    def emit(self, record: logging.LogRecord) -> None:
        """Inject execution_run_id from span context before emitting."""
        try:
            span = trace.get_current_span()
            if span and span.is_recording() and hasattr(span, "attributes"):
                execution_run_id = span.attributes.get("execution.run_id")
                if execution_run_id:
                    record.execution_run_id = execution_run_id
                    if record.msg and isinstance(record.msg, str):
                        try:
                            log_data = json.loads(record.msg)
                            if isinstance(log_data, dict) and "execution_run_id" not in log_data:
                                log_data["execution_run_id"] = execution_run_id
                                record.msg = json.dumps(log_data)
                        except (json.JSONDecodeError, TypeError):
                            pass
        except Exception:
            pass

        self.base_handler.emit(record)


def setup_logging(resource) -> None:
    exporter = _get_log_exporter()
    protocol = get_otel_exporter_otlp_protocol()
    endpoint = get_otel_exporter_otlp_endpoint()
    logs_endpoint = endpoint
    if protocol in ("http/protobuf", "http") and endpoint and not endpoint.endswith("/v1/logs"):
        logs_endpoint = endpoint.rstrip("/") + "/v1/logs"
    if exporter is None:
        if get_effective_otlp_endpoint():
            logging.getLogger(__name__).warning("OTLP log exporter is unavailable")
        return

    try:
        from opentelemetry import _logs
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    except ImportError:
        return

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
    _logs.set_logger_provider(logger_provider)

    base_handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    handler = ExecutionRunIdLoggingHandler(base_handler)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    if not any(isinstance(existing, logging.StreamHandler) for existing in root_logger.handlers):
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(logging.INFO)
        root_logger.addHandler(stream_handler)
    if not any(
        isinstance(existing, (LoggingHandler, ExecutionRunIdLoggingHandler))
        for existing in root_logger.handlers
    ):
        root_logger.addHandler(handler)

    logging.getLogger(__name__).info(
        json.dumps(
            {
                "event": "otel_logging_configured",
                "protocol": protocol,
                "endpoint": endpoint,
                "logs_endpoint": logs_endpoint,
                "exporter": exporter.__class__.__name__,
            }
        )
    )


def ensure_otel_logging(logger_name: str) -> None:
    """Ensure the OTEL logging handler is attached to a logger."""
    try:
        from opentelemetry import _logs
        from opentelemetry.sdk._logs import LoggingHandler
    except ImportError:
        return

    logger = logging.getLogger(logger_name)
    if any(
        isinstance(existing, (LoggingHandler, ExecutionRunIdLoggingHandler))
        for existing in logger.handlers
    ):
        return

    provider = _logs.get_logger_provider()
    if provider is None:
        return

    base_handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
    handler = ExecutionRunIdLoggingHandler(base_handler)
    logger.addHandler(handler)

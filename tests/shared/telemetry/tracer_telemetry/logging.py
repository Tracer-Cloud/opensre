from __future__ import annotations

import json
import logging

from opentelemetry import trace

# Try to import gRPC exporter first, fall back to HTTP if not available
try:
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
except ImportError:
    try:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    except ImportError:
        OTLPLogExporter = None


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
    if OTLPLogExporter is None:
        return

    try:
        from opentelemetry import _logs
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    except ImportError:
        return

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    _logs.set_logger_provider(logger_provider)

    base_handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    handler = ExecutionRunIdLoggingHandler(base_handler)
    root_logger = logging.getLogger()
    if not any(isinstance(existing, (LoggingHandler, ExecutionRunIdLoggingHandler)) for existing in root_logger.handlers):
        root_logger.addHandler(handler)

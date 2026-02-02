from __future__ import annotations

import logging

from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler, set_logger_provider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor


def setup_logging(resource) -> None:
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(logger_provider)

    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    root_logger = logging.getLogger()
    if not any(isinstance(existing, LoggingHandler) for existing in root_logger.handlers):
        root_logger.addHandler(handler)

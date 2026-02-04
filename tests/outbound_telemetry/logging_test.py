import logging
import os
from contextlib import contextmanager

from opentelemetry.sdk.resources import Resource

from app.outbound_telemetry.logging import (
    ExecutionRunIdLoggingHandler,
    ensure_otel_logging,
    setup_logging,
)


@contextmanager
def temp_env(values: dict[str, str]):
    original = os.environ.copy()
    os.environ.update(values)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def test_setup_logging_and_ensure_handler():
    with temp_env(
        {
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
        }
    ):
        setup_logging(Resource.create({}))
        ensure_otel_logging("outbound_telemetry_test")
        logger = logging.getLogger("outbound_telemetry_test")
        assert any(isinstance(handler, ExecutionRunIdLoggingHandler) for handler in logger.handlers)

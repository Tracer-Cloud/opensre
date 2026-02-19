import logging

from opentelemetry.sdk.resources import Resource

from app.outbound_telemetry.logging import (
    ExecutionRunIdLoggingHandler,
    ensure_otel_logging,
    setup_logging,
)
from tests.outbound_telemetry.conftest import temp_env


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

"""Logging configuration for the Telegram gateway process."""

from __future__ import annotations

import logging

from infrastructure.logging.quiet_third_party import quiet_noisy_third_party_loggers

# Routine authorized inbound audits are still emitted at INFO for other surfaces
# (ops tooling) but are hidden in the dedicated gateway process.
_ROUTINE_AUDIT_MARKER = "authorized=True"


class _GatewayLogFormatter(logging.Formatter):
    """Present gateway package logs under a single short logger name."""

    def format(self, record: logging.LogRecord) -> str:
        if record.name == "gateway" or record.name.startswith("gateway."):
            record.name = "gateway"
        return super().format(record)


class _GatewayProcessLogFilter(logging.Filter):
    """Drop high-volume success-path noise from the gateway terminal."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "integrations.messaging_security" and record.levelno <= logging.INFO:
            message = record.getMessage()
            if _ROUTINE_AUDIT_MARKER in message:
                return False
        return True


def configure_logging() -> logging.Logger:
    """Configure root logging for the dedicated Telegram gateway process."""
    gateway_logger = logging.getLogger("gateway")

    if not logging.getLogger().handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            _GatewayLogFormatter(
                fmt="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        handler.addFilter(_GatewayProcessLogFilter())
        logging.basicConfig(level=logging.INFO, handlers=[handler])

    quiet_noisy_third_party_loggers()
    return gateway_logger

"""Errors shared across gateway transports and the composition root."""

from __future__ import annotations


class GatewayConfigurationError(RuntimeError):
    """Raised when a gateway transport's configuration is missing or invalid."""


class GatewayTransportFailedError(RuntimeError):
    """Raised when a transport starts but fails readiness or a runtime check."""


__all__ = ["GatewayConfigurationError", "GatewayTransportFailedError"]

"""OpenSRE hosted gateway: the organization's managed Fargate gateway, reached through the app."""

from integrations.hosted_gateway.client import (
    ERR_INSECURE_APP_URL,
    ERR_INVALID_RESPONSE,
    ERR_NOT_SIGNED_IN,
    ERR_NOT_SUPPORTED,
    ERR_UNAUTHORIZED,
    ERR_UNREACHABLE,
    EXPECTED_ERRORS,
    GatewayHealth,
    HostedGatewayClient,
    HostedGatewayError,
)

__all__ = [
    "ERR_INSECURE_APP_URL",
    "ERR_INVALID_RESPONSE",
    "ERR_NOT_SIGNED_IN",
    "ERR_NOT_SUPPORTED",
    "ERR_UNAUTHORIZED",
    "ERR_UNREACHABLE",
    "EXPECTED_ERRORS",
    "GatewayHealth",
    "HostedGatewayClient",
    "HostedGatewayError",
]

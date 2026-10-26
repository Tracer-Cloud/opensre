"""First-party product analytics delivery constants."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class LLMCreditErrorReason(StrEnum):
    """Recorded billing reasons, distinct from transient provider rate limits."""

    OPENSRE = "opensre_credits_exhausted"
    PROVIDER = "provider_credits_exhausted"


ANALYTICS_DISABLED_ENV: Final[str] = "OPENSRE_ANALYTICS_DISABLED"
ANALYTICS_ENV_ENV: Final[str] = "OPENSRE_ANALYTICS_ENV"
ANALYTICS_TEST_ENV: Final[str] = "OPENSRE_IS_TEST"
ANALYTICS_CICD_ENV: Final[str] = "OPENSRE_CICD"
ANALYTICS_EVENT_SCHEMA_VERSION: Final[int] = 1
ANALYTICS_PROPERTIES_VERSION: Final[int] = 2
ANALYTICS_INGEST_PATH: Final[str] = "/api/analytics/events"

# Installer-provided dimensions consumed by the hidden, non-interactive
# ``opensre --record-install`` path. The first ordinary CLI invocation uses
# safe defaults when an installer did not provide them.
ANALYTICS_INSTALL_SOURCE_ENV: Final[str] = "OPENSRE_INSTALL_SOURCE"
ANALYTICS_INSTALL_CHANNEL_ENV: Final[str] = "OPENSRE_INSTALL_CHANNEL"
ANALYTICS_INSTALL_ORIGIN_ENV: Final[str] = "OPENSRE_INSTALL_ORIGIN"
ANALYTICS_INSTALL_ORIGINS: Final[frozenset[str]] = frozenset(
    {"landing_page", "documentation", "github", "cicd"}
)
ANALYTICS_INSTALL_MARKER_STATE_ENV: Final[str] = "OPENSRE_INSTALL_MARKER_STATE"
ANALYTICS_INSTALL_VERSION_ENV: Final[str] = "OPENSRE_INSTALL_VERSION"
ANALYTICS_LOG_EVENTS_ENV: Final[str] = "OPENSRE_ANALYTICS_LOG_EVENTS"
ANALYTICS_MAX_PAYLOAD_BYTES: Final[int] = 256 * 1024
ANALYTICS_SOURCE: Final[str] = "opensre_runtime"
ANALYTICS_SIGNATURE_HEADER: Final[str] = "X-OpenSRE-Signature"
ANALYTICS_SIGNATURE_VERSION: Final[str] = "v1"
ANALYTICS_TIMESTAMP_HEADER: Final[str] = "X-OpenSRE-Timestamp"
ANALYTICS_RUNNER_TOKEN_HEADER: Final[str] = "X-OpenSRE-Runner-Token"
ANALYTICS_EXECUTION_CONTEXT_ENV: Final[str] = "OPENSRE_EXECUTION_CONTEXT_PATH"
ANALYTICS_EXECUTION_CONTEXT_PATH: Final[str] = "/run/opensre/execution-context.json"
ANALYTICS_RUNNER_INGEST_URL: Final[str] = "https://app.opensre.com/api/analytics/events"
ANALYTICS_RUNNER_AUDIENCE: Final[str] = "https://app.opensre.com/analytics/runner/"

__all__ = [
    "LLMCreditErrorReason",
    "ANALYTICS_CICD_ENV",
    "ANALYTICS_DISABLED_ENV",
    "ANALYTICS_ENV_ENV",
    "ANALYTICS_EVENT_SCHEMA_VERSION",
    "ANALYTICS_PROPERTIES_VERSION",
    "ANALYTICS_INGEST_PATH",
    "ANALYTICS_INSTALL_CHANNEL_ENV",
    "ANALYTICS_INSTALL_ORIGIN_ENV",
    "ANALYTICS_INSTALL_ORIGINS",
    "ANALYTICS_INSTALL_MARKER_STATE_ENV",
    "ANALYTICS_INSTALL_SOURCE_ENV",
    "ANALYTICS_INSTALL_VERSION_ENV",
    "ANALYTICS_LOG_EVENTS_ENV",
    "ANALYTICS_MAX_PAYLOAD_BYTES",
    "ANALYTICS_SIGNATURE_HEADER",
    "ANALYTICS_SIGNATURE_VERSION",
    "ANALYTICS_SOURCE",
    "ANALYTICS_TIMESTAMP_HEADER",
    "ANALYTICS_RUNNER_TOKEN_HEADER",
    "ANALYTICS_EXECUTION_CONTEXT_ENV",
    "ANALYTICS_EXECUTION_CONTEXT_PATH",
    "ANALYTICS_RUNNER_AUDIENCE",
    "ANALYTICS_RUNNER_INGEST_URL",
    "ANALYTICS_TEST_ENV",
]

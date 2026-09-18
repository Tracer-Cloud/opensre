"""First-party product analytics delivery constants."""

from __future__ import annotations

from typing import Final

ANALYTICS_DISABLED_ENV: Final[str] = "OPENSRE_ANALYTICS_DISABLED"
ANALYTICS_EVENT_SCHEMA_VERSION: Final[int] = 1
ANALYTICS_INGEST_PATH: Final[str] = "/api/analytics/events"

# Installer-provided dimensions consumed by the hidden, non-interactive
# ``opensre --record-install`` path. The first ordinary CLI invocation uses
# safe defaults when an installer did not provide them.
ANALYTICS_INSTALL_SOURCE_ENV: Final[str] = "OPENSRE_INSTALL_SOURCE"
ANALYTICS_INSTALL_CHANNEL_ENV: Final[str] = "OPENSRE_INSTALL_CHANNEL"
ANALYTICS_INSTALL_VERSION_ENV: Final[str] = "OPENSRE_INSTALL_VERSION"
ANALYTICS_LOG_EVENTS_ENV: Final[str] = "OPENSRE_ANALYTICS_LOG_EVENTS"
ANALYTICS_MAX_PAYLOAD_BYTES: Final[int] = 256 * 1024
ANALYTICS_SOURCE: Final[str] = "opensre_runtime"
ANALYTICS_SIGNATURE_HEADER: Final[str] = "X-OpenSRE-Signature"
ANALYTICS_SIGNATURE_VERSION: Final[str] = "v1"
ANALYTICS_TIMESTAMP_HEADER: Final[str] = "X-OpenSRE-Timestamp"

__all__ = [
    "ANALYTICS_DISABLED_ENV",
    "ANALYTICS_EVENT_SCHEMA_VERSION",
    "ANALYTICS_INGEST_PATH",
    "ANALYTICS_INSTALL_CHANNEL_ENV",
    "ANALYTICS_INSTALL_SOURCE_ENV",
    "ANALYTICS_INSTALL_VERSION_ENV",
    "ANALYTICS_LOG_EVENTS_ENV",
    "ANALYTICS_MAX_PAYLOAD_BYTES",
    "ANALYTICS_SIGNATURE_HEADER",
    "ANALYTICS_SIGNATURE_VERSION",
    "ANALYTICS_SOURCE",
    "ANALYTICS_TIMESTAMP_HEADER",
]

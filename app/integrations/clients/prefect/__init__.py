"""Prefect API client package."""

from app.integrations.clients.prefect.client import (
    PrefectClient,
    PrefectConfig,
    make_prefect_client,
)

__all__ = ["PrefectClient", "PrefectConfig", "make_prefect_client"]
# Proxy for backward compatibility
from app.services.prefect.client import PrefectClient  # noqa: F401

import warnings

warnings.warn(
    "app.integrations.clients.prefect is deprecated. "
    "Please use app.services.prefect instead.",
    DeprecationWarning,
    stacklevel=2
)

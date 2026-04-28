"""Prefect API client package."""

from __future__ import annotations

import warnings

from app.integrations.clients.prefect.client import (
    PrefectClient,
    PrefectConfig,
    make_prefect_client,
)

__all__ = ["PrefectClient", "PrefectConfig", "make_prefect_client"]

warnings.warn(
    "app.integrations.clients.prefect is deprecated. Please use app.services.prefect instead.",
    DeprecationWarning,
    stacklevel=2,
)

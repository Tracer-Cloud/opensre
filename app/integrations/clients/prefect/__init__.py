"""Compatibility shim for moved Prefect client."""

import warnings

from app.services.prefect import PrefectClient, PrefectConfig, make_prefect_client

warnings.warn(
    "app.integrations.clients.prefect is deprecated. Use app.services.prefect instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["PrefectClient", "PrefectConfig", "make_prefect_client"]

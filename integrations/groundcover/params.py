"""Parameter extraction and time window formatting for groundcover tools."""

from __future__ import annotations

from typing import Any

from integrations.groundcover.client_factory import client_for_source


def time_range(start: str, end: str, period: str) -> dict[str, str]:
    """Echo the requested time window; period defaults to the server default (1h)."""
    return {
        "start": start or "",
        "end": end or "",
        "period": period or ("" if (start and end) else "PT1H"),
    }


def base_extract_params(
    gc: dict[str, Any],
    *,
    default_query: str | None = None,
    include_period: bool = True,
) -> dict[str, Any]:
    """Inject a pre-built client + optional fixture backend, never raw secrets.

    Credentials are bound here into a runtime ``GroundcoverClient`` object so the
    model never sees or can override them. The ``_groundcover_client`` and
    ``groundcover_backend`` keys are runtime objects that the seed-input
    redactor (``^_`` / ``*backend`` patterns) strips before schema validation.
    Only real objects (and schema-declared fields) are included so
    ``additionalProperties: false`` schemas accept the seed input. Tools without
    a time window (entities/monitors/reference) pass ``include_period=False``.
    """
    params: dict[str, Any] = {}
    if include_period:
        params["period"] = gc.get("period", "PT1H")
    if default_query is not None:
        params["query"] = gc.get("default_query") or default_query
    backend = gc.get("_backend")
    if backend is not None:
        params["groundcover_backend"] = backend
    client = client_for_source(gc)
    if client is not None:
        params["_groundcover_client"] = client
    return params

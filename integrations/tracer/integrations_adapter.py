"""Adapter: wire ``TracerClient.get_all_integrations`` into the
:mod:`infrastructure.harness_providers` remote-fetch port.

Lives in ``integrations/tracer/`` so the Tracer-specific dependency stays
inside the Tracer integration package. Core code calls
:func:`infrastructure.harness_providers.fetch_remote_integrations`; the boundary
(``surfaces.shared.terminal.output.boundary``) registers this
adapter at startup so the call routes through ``TracerClient``.
"""

from __future__ import annotations

from typing import Any

from config.constants.account import INTEGRATION_APP_ORIGIN, INTEGRATION_RETRIEVAL_ORIGIN_FIELD
from integrations.tracer import get_tracer_client_for_org


def fetch_tracer_remote_integrations(org_id: str, auth_token: str) -> list[dict[str, Any]]:
    """Fetch a user's remote integrations from Tracer Cloud.

    Matches :data:`infrastructure.harness_providers.RemoteIntegrationsFetcher`.
    Any exception (network, auth, schema) propagates to the caller —
    ``resolve_integrations`` already has the try/except + local
    fall-through logic.
    """
    return [
        {**record, INTEGRATION_RETRIEVAL_ORIGIN_FIELD: INTEGRATION_APP_ORIGIN}
        for record in get_tracer_client_for_org(org_id, auth_token).get_all_integrations()
    ]

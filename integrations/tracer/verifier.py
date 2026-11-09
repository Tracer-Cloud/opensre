"""Tracer (hosted control plane) integration verifier."""

from __future__ import annotations

from typing import Any

from config.tracer_urls import get_tracer_base_url
from infrastructure.safety.auth.jwt_auth import extract_org_id_from_jwt
from integrations.config_models import TracerIntegrationConfig
from integrations.tracer.client import TracerClient
from integrations.verification import register_verifier, result


@register_verifier("tracer")
def verify_tracer(source: str, config: dict[str, Any]) -> dict[str, str]:
    try:
        tracer_config = TracerIntegrationConfig.model_validate(config)
    except Exception as err:
        return result("tracer", source, "missing", str(err))
    if not tracer_config.jwt_token:
        return result("tracer", source, "missing", "Missing JWT token.")

    base_url = tracer_config.base_url or get_tracer_base_url()
    try:
        org_id = extract_org_id_from_jwt(tracer_config.jwt_token)
    except Exception as err:
        return result("tracer", source, "failed", f"JWT decode failed: {err}")
    if not org_id:
        return result("tracer", source, "failed", "JWT did not contain an org identifier.")

    try:
        tracer_client = TracerClient(
            base_url=base_url,
            org_id=org_id,
            jwt_token=tracer_config.jwt_token,
        )
        integrations = tracer_client.get_all_integrations()
    except Exception as err:
        return result("tracer", source, "failed", f"Tracer API check failed: {err}")

    return result(
        "tracer",
        source,
        "passed",
        f"Connected to {base_url} for org {org_id} and listed {len(integrations)} integrations.",
    )

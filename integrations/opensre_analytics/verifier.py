"""OpenSRE Analytics integration verifier."""

from __future__ import annotations

from integrations.opensre_analytics.client import validate_config
from integrations.opensre_analytics.config import SERVICE, build_opensre_analytics_config
from integrations.verification import register_validation_verifier

verify_opensre_analytics = register_validation_verifier(
    SERVICE,
    build_config=build_opensre_analytics_config,
    validate_config=validate_config,
)

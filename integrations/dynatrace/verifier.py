"""Dynatrace integration verifier."""

from __future__ import annotations

from integrations.dynatrace.client import DynatraceClient
from integrations.dynatrace.config import DynatraceIntegrationConfig
from integrations.verification import register_probe_verifier

verify_dynatrace = register_probe_verifier(
    "dynatrace",
    config=DynatraceIntegrationConfig.model_validate,
    client=DynatraceClient,
)

__all__ = ["verify_dynatrace"]

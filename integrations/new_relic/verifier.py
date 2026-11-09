"""New Relic integration verifier."""

from __future__ import annotations

from integrations.new_relic.client import NewRelicClient
from integrations.new_relic.config import NewRelicIntegrationConfig
from integrations.verification import register_probe_verifier

verify_new_relic = register_probe_verifier(
    "new_relic",
    config=NewRelicIntegrationConfig.model_validate,
    client=NewRelicClient,
)

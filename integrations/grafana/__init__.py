"""Grafana integration classifier.

Splits a validated :class:`GrafanaIntegrationConfig` into one of two resolved
keys so the rest of the stack can treat local and cloud Grafana separately:

* ``grafana`` — a remote/cloud instance authenticated with a service account
  token.
* ``grafana_local`` — a locally hosted instance (localhost / loopback), whether
  it authenticates with a token, basic auth, or anonymously.

Authentication details (token vs basic vs anonymous) are owned by
``GrafanaIntegrationConfig``; this module only decides local vs cloud routing.

The client and ``GRAFANA_SETUP`` are re-exported lazily: harness registration
imports small leaves of this package at every CLI startup, and those must not
pull the HTTP client or the setup/verifier chain.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING, Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import GrafanaIntegrationConfig

if TYPE_CHECKING:
    from integrations.grafana.client import get_grafana_client_from_credentials
    from integrations.grafana.setup import GRAFANA_SETUP

#: Public name -> the submodule that defines it, imported on first access.
_LAZY_EXPORTS: dict[str, str] = {
    "GRAFANA_SETUP": "integrations.grafana.setup",
    "get_grafana_client_from_credentials": "integrations.grafana.client",
}

logger = logging.getLogger(__name__)


def __getattr__(name: str) -> object:
    """Resolve a lazily re-exported name to its submodule attribute (PEP 562)."""
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module_path), name)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[GrafanaIntegrationConfig | None, str | None]:
    try:
        cfg = GrafanaIntegrationConfig.model_validate(
            {
                "endpoint": credentials.get("endpoint", ""),
                "api_key": credentials.get("api_key", ""),
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
                "verify_ssl": credentials.get("verify_ssl", True),
                "ca_bundle": credentials.get("ca_bundle", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="grafana", record_id=record_id)
        return None, None
    if not cfg.endpoint:
        return None, None
    if cfg.is_local:
        if cfg.is_anonymous_local:
            # Drop the "local" sentinel so downstream auth stays anonymous.
            return cfg.model_copy(update={"api_key": ""}), "grafana_local"
        return cfg, "grafana_local"
    if cfg.has_token:
        return cfg, "grafana"
    return None, None


__all__ = ["GRAFANA_SETUP", "classify", "get_grafana_client_from_credentials"]

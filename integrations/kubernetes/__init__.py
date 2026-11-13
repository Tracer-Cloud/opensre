"""Kubernetes integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import KubernetesIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[KubernetesIntegrationConfig | None, str | None]:
    try:
        cfg = KubernetesIntegrationConfig.model_validate(
            {
                "kubeconfig": credentials.get("kubeconfig", ""),
                "kubeconfig_path": credentials.get("kubeconfig_path", ""),
                "context": credentials.get("context", ""),
                "namespace": credentials.get("namespace", "default"),
                "allowed_namespaces": credentials.get("allowed_namespaces", ()),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="kubernetes", record_id=record_id)
        return None, None
    if cfg.is_configured:
        return cfg, "kubernetes"
    return None, None

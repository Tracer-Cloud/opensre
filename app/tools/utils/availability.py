"""Shared tool-availability helpers for backend-aware integration sources.

Tools that can delegate to a pre-injected fixture backend — as the
synthetic harnesses under ``tests/synthetic/`` do — need an availability
check that accepts either real connection-verified credentials or an
injected ``_backend`` object.  Centralising those helpers here avoids
cross-tool imports (e.g. ``DataDogMonitorsTool`` reaching into
``DataDogLogsTool`` to borrow a helper) and keeps the pattern consistent
across integrations.
"""

from __future__ import annotations


def eks_available_or_backend(sources: dict[str, dict]) -> bool:
    """Available when real EKS credentials are present OR a fixture backend is injected.

    Used by EKS tool wrappers whose ``extract_params`` can delegate to a
    mock ``eks_backend`` for synthetic tests.  Tools without backend
    support continue to use the narrower check in
    ``app.tools.EKSListClustersTool._eks_available``.
    """
    eks = sources.get("eks", {})
    return bool(eks.get("connection_verified") or eks.get("_backend"))


def datadog_available_or_backend(sources: dict[str, dict]) -> bool:
    """Available when real Datadog credentials are present OR a fixture backend is injected.

    Used by Datadog tool wrappers whose ``extract_params`` can delegate
    to a mock ``datadog_backend`` for synthetic tests.
    """
    dd = sources.get("datadog", {})
    return bool(dd.get("connection_verified") or dd.get("_backend"))


def cloudwatch_is_available(sources: dict[str, dict]) -> bool:
    """Available when a CloudWatch source is present in the alert context.

    CloudWatch uses IAM-based auth so detect_sources never writes
    connection_verified — availability is gated on the source key existing
    (populated when cloudwatch_log_group is present in alert annotations).
    Tool params like ``job_queue`` are alert-specific and provided by the LLM.
    """
    return bool(sources.get("cloudwatch"))

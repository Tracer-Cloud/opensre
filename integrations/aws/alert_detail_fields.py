"""AWS/EKS/Kubernetes alert-detail field registration.

Registers the optional ``AlertDetails`` fields owned by AWS and Kubernetes
alert sources (``cloudwatch_log_group``, ``eks_cluster``, ``kube_namespace``,
``pod_name``, ``deployment``) so alert extraction can read them without
core hardcoding vendor field names. There is no standalone ``integrations/kubernetes/`` package yet — EKS
is the only Kubernetes alert source this repo wires up today — so the
Kubernetes-owned fields live here alongside the AWS ones.

Registered from ``integrations/harness_adapters.py`` via
:func:`core.domain.alerts.extraction.register_alert_detail_fields`.
"""

from __future__ import annotations

ALERT_DETAIL_FIELDS: tuple[str, ...] = (
    "cloudwatch_log_group",
    "eks_cluster",
    "kube_namespace",
    "pod_name",
    "deployment",
)

__all__ = ["ALERT_DETAIL_FIELDS"]

"""Tests for Argo CD source detection in detect_sources."""

from __future__ import annotations

from app.nodes.plan_actions.detect_sources import detect_sources

_ARGOCD_INT = {
    "base_url": "https://argocd.example.com",
    "bearer_token": "tok_test",
    "username": "",
    "password": "",
    "project": "default",
    "app_namespace": "argocd",
    "verify_ssl": True,
    "integration_id": "argocd-1",
}


def test_argocd_source_detected_from_prefixed_annotations() -> None:
    alert = {
        "annotations": {
            "argocd_application": "payments-api",
            "argocd_project": "payments",
            "argocd_app_namespace": "argocd-system",
        }
    }

    sources = detect_sources(alert, {}, {"argocd": _ARGOCD_INT})

    argocd = sources.get("argocd")
    assert argocd is not None
    assert argocd["base_url"] == "https://argocd.example.com"
    assert argocd["bearer_token"] == "tok_test"
    assert argocd["application_name"] == "payments-api"
    assert argocd["project"] == "payments"
    assert argocd["app_namespace"] == "argocd-system"
    assert argocd["connection_verified"] is True


def test_argocd_source_created_when_configured_even_without_app_hint() -> None:
    alert = {"annotations": {"summary": "deployment drift detected in GitOps control plane"}}

    sources = detect_sources(alert, {}, {"argocd": _ARGOCD_INT})

    argocd = sources.get("argocd")
    assert argocd is not None
    assert argocd["application_name"] == ""
    assert argocd["project"] == "default"


def test_argocd_source_detected_from_argocd_specific_hint() -> None:
    alert = {"annotations": {"summary": "Argo-CD application is OutOfSync"}}

    sources = detect_sources(alert, {}, {"argocd": _ARGOCD_INT})

    assert "argocd" in sources


def test_argocd_source_not_created_for_generic_deployment_alert() -> None:
    alert = {"annotations": {"summary": "EKS pod deployment failed during rollout"}}

    sources = detect_sources(alert, {}, {"argocd": _ARGOCD_INT})

    assert "argocd" not in sources


def test_argocd_source_not_created_without_integration() -> None:
    alert = {"annotations": {"argocd_application": "payments-api"}}

    sources = detect_sources(alert, {}, {})

    assert "argocd" not in sources


def test_argocd_source_supports_top_level_application_fields() -> None:
    alert = {
        "argocd_app": "checkout-api",
        "argocd_revision": "abc123",
        "annotations": {},
    }

    sources = detect_sources(alert, {}, {"argocd": _ARGOCD_INT})

    argocd = sources["argocd"]
    assert argocd["application_name"] == "checkout-api"
    assert argocd["revision"] == "abc123"

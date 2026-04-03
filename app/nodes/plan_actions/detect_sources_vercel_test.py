"""Tests for Vercel source detection in detect_sources."""

from __future__ import annotations

from app.nodes.plan_actions.detect_sources import detect_sources

_VERCEL_INT = {
    "api_token": "tok_test",
    "team_id": "team_abc",
    "integration_id": "vercel-1",
}


def test_vercel_source_detected_from_annotations() -> None:
    alert = {
        "annotations": {
            "vercel_project_id": "proj_frontend",
            "vercel_deployment_id": "dpl_abc123",
        }
    }
    sources = detect_sources(alert, {}, {"vercel": _VERCEL_INT})

    vercel = sources.get("vercel")
    assert vercel is not None
    assert vercel["project_id"] == "proj_frontend"
    assert vercel["deployment_id"] == "dpl_abc123"
    assert vercel["api_token"] == "tok_test"
    assert vercel["team_id"] == "team_abc"
    assert vercel["connection_verified"] is True


def test_vercel_source_detected_with_generic_project_id_key() -> None:
    alert = {"annotations": {"project_id": "proj_api"}}
    sources = detect_sources(alert, {}, {"vercel": _VERCEL_INT})
    assert sources.get("vercel", {}).get("project_id") == "proj_api"


def test_vercel_source_detected_with_generic_deployment_id_key() -> None:
    alert = {"annotations": {"deployment_id": "dpl_xyz"}}
    sources = detect_sources(alert, {}, {"vercel": _VERCEL_INT})
    assert sources.get("vercel", {}).get("deployment_id") == "dpl_xyz"


def test_vercel_source_not_created_when_no_vercel_annotations() -> None:
    alert = {
        "annotations": {
            "cloudwatch_log_group": "/aws/lambda/fn",
            "s3_bucket": "my-bucket",
        }
    }
    sources = detect_sources(alert, {}, {"vercel": _VERCEL_INT})
    assert "vercel" not in sources


def test_vercel_source_not_created_without_integration() -> None:
    alert = {"annotations": {"vercel_project_id": "proj_frontend"}}
    sources = detect_sources(alert, {}, {})
    assert "vercel" not in sources


def test_vercel_source_not_created_when_no_resolved_integrations() -> None:
    alert = {"annotations": {"vercel_project_id": "proj_frontend"}}
    sources = detect_sources(alert, {}, None)
    assert "vercel" not in sources


def test_vercel_source_detects_top_level_alert_fields() -> None:
    alert = {
        "vercel_project_id": "proj_frontend",
        "vercel_deployment_id": "dpl_from_top_level",
        "annotations": {},
    }
    sources = detect_sources(alert, {}, {"vercel": _VERCEL_INT})
    vercel = sources.get("vercel")
    assert vercel is not None
    assert vercel["deployment_id"] == "dpl_from_top_level"

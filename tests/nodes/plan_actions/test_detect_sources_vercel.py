"""Tests for Vercel source detection in detect_sources."""

from __future__ import annotations

from app.nodes.plan_actions.detect_sources import detect_sources

_VERCEL_INT = {
    "api_token": "tok_test",
    "team_id": "team_abc",
    "integration_id": "vercel-1",
}

_GITHUB_INT = {
    "url": "http://github.example.com/mcp",
    "mode": "streamable-http",
    "auth_token": "ghp_test",
    "command": "",
    "args": [],
}


def test_vercel_source_always_created_when_integration_configured() -> None:
    # Vercel source is created whenever the integration is present, regardless of annotations
    alert = {
        "annotations": {
            "cloudwatch_log_group": "/aws/lambda/fn",
            "s3_bucket": "my-bucket",
        }
    }
    sources = detect_sources(alert, {}, {"vercel": _VERCEL_INT})
    vercel = sources.get("vercel")
    assert vercel is not None
    assert vercel["connection_verified"] is True
    assert vercel["api_token"] == "tok_test"
    assert vercel["project_id"] == ""
    assert vercel["deployment_id"] == ""


def test_vercel_source_detected_from_prefixed_annotations() -> None:
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


def test_vercel_source_ignores_generic_project_id_key() -> None:
    # Generic project_id must NOT be used — it collides with Kubernetes/Datadog annotations
    alert = {"annotations": {"project_id": "proj_api"}}
    sources = detect_sources(alert, {}, {"vercel": _VERCEL_INT})
    assert sources.get("vercel", {}).get("project_id") == ""


def test_vercel_source_ignores_generic_deployment_id_key() -> None:
    # Generic deployment_id must NOT be used — it collides with other integrations
    alert = {"annotations": {"deployment_id": "dpl_xyz"}}
    sources = detect_sources(alert, {}, {"vercel": _VERCEL_INT})
    assert sources.get("vercel", {}).get("deployment_id") == ""


def test_vercel_source_not_created_without_integration() -> None:
    alert = {"annotations": {"vercel_project_id": "proj_frontend"}}
    sources = detect_sources(alert, {}, {})
    assert "vercel" not in sources


def test_vercel_source_not_created_when_no_resolved_integrations() -> None:
    alert = {"annotations": {"vercel_project_id": "proj_frontend"}}
    sources = detect_sources(alert, {}, None)
    assert "vercel" not in sources


def test_vercel_source_not_created_when_token_missing() -> None:
    # Integration entry without api_token should not create a vercel source
    alert = {"annotations": {"vercel_project_id": "proj_x"}}
    sources = detect_sources(alert, {}, {"vercel": {"api_token": "", "team_id": ""}})
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


def test_vercel_github_metadata_enables_github_source_when_integration_configured() -> None:
    alert = {
        "vercel_project_id": "proj_frontend",
        "vercel_deployment_id": "dpl_from_top_level",
        "vercel_github_repo": "org/tracer-marketing-website-v3",
        "vercel_github_commit_sha": "abc123",
        "vercel_github_commit_ref": "main",
        "error_message": "Build failed: cannot resolve import",
        "annotations": {},
    }
    sources = detect_sources(alert, {}, {"vercel": _VERCEL_INT, "github": _GITHUB_INT})

    github = sources.get("github")
    vercel = sources.get("vercel")
    assert github is not None
    assert github["owner"] == "org"
    assert github["repo"] == "tracer-marketing-website-v3"
    assert github["sha"] == "abc123"
    assert github["ref"] == "main"
    assert github["query"] == "Build failed: cannot resolve import"
    assert vercel is not None
    assert vercel["github_repo"] == "org/tracer-marketing-website-v3"

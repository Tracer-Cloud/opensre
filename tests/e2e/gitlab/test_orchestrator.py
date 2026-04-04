"""
GitLab Integration E2E Tests.

Validates GitLab connectivity, tool execution, and end-to-end investigation flow.

Required env vars:
    GITLAB_ACCESS_TOKEN  - Personal access token with read_api scope
    GITLAB_PROJECT_ID    - Project to use for investigation (e.g. "myorg/myrepo")

Optional env vars:
    GITLAB_BASE_URL      - GitLab instance URL (defaults to https://gitlab.com/api/v4)
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from app.integrations.gitlab import (
    DEFAULT_GITLAB_BASE_URL,
    build_gitlab_config,
    get_gitlab_commits,
    get_gitlab_mrs,
    get_gitlab_pipelines,
    validate_gitlab_config,
)
from tests.utils.alert_factory import create_alert


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_env() -> tuple[str, str, str]:
    """Return (access_token, base_url, project_id) or skip the test."""
    access_token = os.getenv("GITLAB_ACCESS_TOKEN", "").strip()
    project_id = os.getenv("GITLAB_PROJECT_ID", "").strip()
    base_url = os.getenv("GITLAB_BASE_URL", DEFAULT_GITLAB_BASE_URL).strip() or DEFAULT_GITLAB_BASE_URL

    missing = []
    if not access_token:
        missing.append("GITLAB_ACCESS_TOKEN")
    if not project_id:
        missing.append("GITLAB_PROJECT_ID")

    if missing:
        pytest.skip(f"GitLab env vars not set: {', '.join(missing)}")

    return access_token, base_url, project_id


def _gitlab_config(access_token: str, base_url: str):
    return build_gitlab_config({"base_url": base_url, "auth_token": access_token})


# ---------------------------------------------------------------------------
# 1. Connectivity
# ---------------------------------------------------------------------------


def test_gitlab_connectivity():
    """Verify that the token authenticates and /user responds."""
    access_token, base_url, _ = _require_env()
    config = _gitlab_config(access_token, base_url)

    result = validate_gitlab_config(config)

    assert result.ok, f"GitLab connectivity failed: {result.detail}"
    assert "@" in result.detail or "Authenticated" in result.detail, (
        f"Expected username in detail, got: {result.detail}"
    )


# ---------------------------------------------------------------------------
# 2. Tool-level: commits
# ---------------------------------------------------------------------------


def test_gitlab_list_commits():
    """Fetch recent commits for the configured project."""
    access_token, base_url, project_id = _require_env()
    config = _gitlab_config(access_token, base_url)

    since = datetime(2020, 1, 1, tzinfo=UTC).isoformat()
    commits = get_gitlab_commits(
        config=config,
        project_id=project_id,
        since=since,
        per_page=5,
    )

    assert isinstance(commits, list), "Expected a list of commits"
    if commits:
        first = commits[0]
        assert "id" in first or "short_id" in first, f"Unexpected commit shape: {first.keys()}"


# ---------------------------------------------------------------------------
# 3. Tool-level: merge requests
# ---------------------------------------------------------------------------


def test_gitlab_list_mrs():
    """Fetch merge requests for the configured project."""
    access_token, base_url, project_id = _require_env()
    config = _gitlab_config(access_token, base_url)

    updated_after = datetime(2020, 1, 1, tzinfo=UTC).isoformat()
    mrs = get_gitlab_mrs(
        config=config,
        project_id=project_id,
        state="merged",
        updated_after=updated_after,
        per_page=5,
    )

    assert isinstance(mrs, list), "Expected a list of merge requests"
    if mrs:
        first = mrs[0]
        assert "iid" in first or "id" in first, f"Unexpected MR shape: {first.keys()}"


# ---------------------------------------------------------------------------
# 4. Tool-level: pipelines
# ---------------------------------------------------------------------------


def test_gitlab_list_pipelines():
    """Fetch CI/CD pipelines for the configured project."""
    access_token, base_url, project_id = _require_env()
    config = _gitlab_config(access_token, base_url)

    updated_after = datetime(2020, 1, 1, tzinfo=UTC).isoformat()
    pipelines = get_gitlab_pipelines(
        config=config,
        project_id=project_id,
        updated_after=updated_after,
        per_page=5,
    )

    assert isinstance(pipelines, list), "Expected a list of pipelines"
    if pipelines:
        first = pipelines[0]
        assert "id" in first, f"Unexpected pipeline shape: {first.keys()}"
        assert "status" in first, f"Pipeline missing status: {first.keys()}"


# ---------------------------------------------------------------------------
# 5. End-to-end investigation
# ---------------------------------------------------------------------------


def test_gitlab_investigation_e2e():
    """
    Full investigation flow with GitLab as the evidence source.

    Creates a synthetic alert pointing at the configured GitLab project,
    runs the investigation graph, and asserts that a root cause was produced.
    """
    access_token, base_url, project_id = _require_env()

    from app.cli.investigate import run_investigation_cli

    pipeline_name = "gitlab_ci_pipeline_failure"
    run_id = f"run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

    raw_alert = create_alert(
        pipeline_name=pipeline_name,
        run_name=run_id,
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
        severity="critical",
        annotations={
            "gitlab_project": project_id,
            "branch": "main",
            "error": "CI pipeline failed on main branch",
            "correlation_id": run_id,
        },
    )

    print(f"\nRunning GitLab investigation for project: {project_id}")

    investigation_result = run_investigation_cli(
        alert_name=f"Pipeline failure: {pipeline_name}",
        pipeline_name=pipeline_name,
        severity="critical",
        raw_alert=raw_alert,
    )

    root_cause = investigation_result.get("root_cause", "")
    remediation_steps = investigation_result.get("remediation_steps", [])

    print(f"Root cause: {root_cause}")
    print(f"Remediation steps: {remediation_steps}")

    assert root_cause, (
        "Investigation produced no root cause. "
        "Check that GITLAB_ACCESS_TOKEN has read_api scope and GITLAB_PROJECT_ID is valid."
    )

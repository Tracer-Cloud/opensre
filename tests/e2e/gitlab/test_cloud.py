"""
GitLab Integration E2E Tests (cloud-hosted GitLab source).

GitLab is a supplementary investigation source, not a primary alert source.
These tests mirror how GitHub is used: a production alert fires (Grafana/generic),
and the agent queries GitLab to correlate recent commits, MRs, and pipelines.

Required env vars:
    GITLAB_ACCESS_TOKEN  - Personal access token with read_api + read_repository scope
    GITLAB_PROJECT_ID    - Project path (e.g. "myorg/myrepo")

Optional env vars:
    GITLAB_BASE_URL      - GitLab instance URL (defaults to https://gitlab.com/api/v4)
    GITLAB_MR_IID        - MR IID to post a note on (required only for test_gitlab_post_mr_note)
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.integrations.gitlab import (
    DEFAULT_GITLAB_BASE_URL,
    build_gitlab_config,
    get_gitlab_commits,
    get_gitlab_mrs,
    get_gitlab_pipelines,
    post_gitlab_mr_note,
    validate_gitlab_config,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_env() -> tuple[str, str, str]:
    """Return (access_token, base_url, project_id) or skip the test."""
    access_token = os.getenv("GITLAB_ACCESS_TOKEN", "").strip()
    project_id = os.getenv("GITLAB_PROJECT_ID", "").strip()
    base_url = (
        os.getenv("GITLAB_BASE_URL", DEFAULT_GITLAB_BASE_URL).strip() or DEFAULT_GITLAB_BASE_URL
    )

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


def _gitlab_project_url(base_url: str, project_id: str) -> str:
    """Build a full GitLab project URL from base_url + project path."""
    instance = base_url.replace("/api/v4", "").rstrip("/")
    return f"{instance}/{project_id}"


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
# 5. End-to-end investigation — GitLab as supplementary evidence source
#
# This is the realistic production path: a Grafana-style alert fires (high
# error rate, service degradation, etc.) and the agent also queries GitLab
# to correlate recent commits and MRs that may have caused it.
#
# detect_sources.py picks up GitLab via repo_url or annotations["repository"],
# exactly the same way GitHub is detected — not from a "gitlab_ci" alert.
# ---------------------------------------------------------------------------


def test_gitlab_investigation_e2e():
    """
    Full investigation flow with GitLab as a supplementary evidence source.

    Simulates a Grafana-style production alert that includes a repo_url
    pointing at the configured GitLab project. The agent is expected to use
    GitLab tools (commits, MRs) alongside the alert context to produce a
    root cause, mirroring how GitHub is used in kubernetes/datadog e2e tests.
    """
    access_token, base_url, project_id = _require_env()

    from app.cli.investigate import run_investigation_cli

    # Load the shared fixture and patch in the real project URL so the
    # detect_sources.py repo_url parser picks up the configured project.
    fixture_path = FIXTURES_DIR / "gitlab_high_error_rate_alert.json"
    raw_alert = json.loads(fixture_path.read_text())
    repo_url = _gitlab_project_url(base_url, project_id)
    for alert in raw_alert.get("alerts", []):
        alert.setdefault("annotations", {})["repo_url"] = repo_url
    raw_alert.setdefault("commonAnnotations", {})["repo_url"] = repo_url

    print(f"\nRunning investigation — primary: Grafana alert, supplementary: GitLab ({project_id})")

    investigation_result = run_investigation_cli(
        alert_name="High error rate: api_service",
        pipeline_name="api_service",
        severity="critical",
        raw_alert=raw_alert,
    )

    root_cause = investigation_result.get("root_cause", "")
    remediation_steps = investigation_result.get("remediation_steps", [])

    print(f"Root cause: {root_cause}")
    print(f"Remediation steps: {remediation_steps}")

    assert root_cause, (
        "Investigation produced no root cause. "
        "Check that GITLAB_ACCESS_TOKEN has read_api scope, GITLAB_PROJECT_ID is valid, "
        "and an LLM key (ANTHROPIC_API_KEY or OPENAI_API_KEY) is set."
    )


# ---------------------------------------------------------------------------
# 6. Tool-level: post MR note
# ---------------------------------------------------------------------------


def test_gitlab_post_mr_note():
    """Post a comment on a real MR and verify the note is created.

    Reads project and MR context from the fixture annotations, the same way
    the agent receives them from a real CI/CD alert.

    Required env vars:
        GITLAB_MR_IID  - IID (not global ID) of an open MR in the configured project
    """
    access_token, base_url, project_id = _require_env()

    mr_iid = os.getenv("GITLAB_MR_IID", "").strip()
    if not mr_iid:
        pytest.skip("GITLAB_MR_IID env var not set")

    # Load fixture and inject env values into annotations — same pattern as
    # test_gitlab_investigation_e2e, mirroring how a real alert carries this context.
    fixture_path = FIXTURES_DIR / "gitlab_high_error_rate_alert.json"
    raw_alert = json.loads(fixture_path.read_text())
    repo_url = _gitlab_project_url(base_url, project_id)
    for alert in raw_alert.get("alerts", []):
        alert.setdefault("annotations", {})["repo_url"] = repo_url
        alert["annotations"]["mr_iid"] = mr_iid
    raw_alert.setdefault("commonAnnotations", {})["repo_url"] = repo_url
    raw_alert["commonAnnotations"]["mr_iid"] = mr_iid

    # Read back from annotations — the same way the agent would in production.
    annotations = raw_alert["alerts"][0]["annotations"]
    project_id_from_annotation = project_id  # derived from repo_url by detect_sources
    mr_iid_from_annotation = annotations["mr_iid"]

    config = _gitlab_config(access_token, base_url)
    body = "[opensre e2e test] Automated RCA comment — safe to ignore."
    result = post_gitlab_mr_note(
        config=config,
        project_id=project_id_from_annotation,
        mr_iid=mr_iid_from_annotation,
        body=body,
    )

    assert isinstance(result, dict), f"Expected a dict response, got: {type(result)}"
    assert "id" in result, f"Note response missing 'id': {result.keys()}"
    assert result.get("body") == body, f"Posted body mismatch: {result.get('body')!r}"

    print(
        f"\nPosted note id={result['id']} on MR !{mr_iid_from_annotation} in {project_id_from_annotation}"
    )

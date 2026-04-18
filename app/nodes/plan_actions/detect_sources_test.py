"""Tests for detect_sources — focused on GitLab merge_request_iid extraction."""

from __future__ import annotations

from app.nodes.plan_actions.detect_sources import detect_sources

_GITLAB_INTEGRATION = {
    "gitlab": {
        "base_url": "https://gitlab.example.com/api/v4",
        "auth_token": "gl-token",
    }
}

_BASE_ALERT = {"gitlab_project": "my-org/my-repo"}


def test_detect_sources_gitlab_extracts_mr_iid_from_annotations() -> None:
    raw_alert = {**_BASE_ALERT, "annotations": {"mr_iid": "42"}}

    sources = detect_sources(raw_alert, {}, resolved_integrations=_GITLAB_INTEGRATION)

    assert sources["gitlab"]["merge_request_iid"] == "42"


def test_detect_sources_gitlab_mr_iid_empty_when_not_in_alert() -> None:
    raw_alert = _BASE_ALERT

    sources = detect_sources(raw_alert, {}, resolved_integrations=_GITLAB_INTEGRATION)

    assert sources["gitlab"]["merge_request_iid"] == ""


def test_detect_sources_gitlab_mr_iid_strips_whitespace() -> None:
    raw_alert = {**_BASE_ALERT, "annotations": {"mr_iid": "  7  "}}

    sources = detect_sources(raw_alert, {}, resolved_integrations=_GITLAB_INTEGRATION)

    assert sources["gitlab"]["merge_request_iid"] == "7"


def test_detect_sources_gitlab_not_added_when_no_project_id() -> None:
    raw_alert = {"annotations": {"mr_iid": "42"}}  # no project_id

    sources = detect_sources(raw_alert, {}, resolved_integrations=_GITLAB_INTEGRATION)

    assert "gitlab" not in sources


def test_detect_sources_gitlab_not_added_when_no_integration() -> None:
    raw_alert = _BASE_ALERT

    sources = detect_sources(raw_alert, {}, resolved_integrations={})

    assert "gitlab" not in sources

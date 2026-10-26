"""Tests for SentrySearchIssuesTool (function-based, @tool decorated)."""

from __future__ import annotations

import os
from http import HTTPStatus
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from integrations.sentry import (
    _MAX_SENTRY_QUERY_LEN,
    SentryConfig,
    _sanitize_sentry_query,
    _sentry_query_candidates,
    describe_sentry_api_error,
    list_sentry_issues,
)
from integrations.sentry.tools.sentry_search_issues_tool import (
    _map_search_sentry_issues,
    search_sentry_issues,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestSentrySearchIssuesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return search_sentry_issues.__opensre_registered_tool__


def test_is_available_requires_connection_verified() -> None:
    rt = search_sentry_issues.__opensre_registered_tool__
    assert rt.is_available({"sentry": {"connection_verified": True}}) is True
    assert rt.is_available({"sentry": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = search_sentry_issues.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["organization_slug"] == "my-org"
    assert params["sentry_token"] == "sntryu_test"


def test_extract_params_maps_resolved_config_dump_shape() -> None:
    """Regression: the resolved ``sentry`` source dict is a ``SentryConfig`` dump,
    so its keys are ``auth_token`` / ``base_url`` — NOT ``sentry_token`` /
    ``sentry_url``. Hard-indexing ``sentry['sentry_token']`` raised a KeyError
    that aborted every Sentry query in the gather/investigation loop."""
    from integrations.sentry import SentryConfig
    from integrations.sentry.tools.sentry_search_issues_tool import _search_issues_extract_params

    sources = {
        "sentry": SentryConfig(
            base_url="https://sentry.example.com",
            organization_slug="acme",
            auth_token="sntryu_resolved",
        ).model_dump()
        | {"connection_verified": True}
    }

    params = _search_issues_extract_params(sources)

    assert params["organization_slug"] == "acme"
    assert params["sentry_token"] == "sntryu_resolved"
    assert params["sentry_url"] == "https://sentry.example.com"


def test_run_returns_unavailable_when_no_config() -> None:
    # Stub env resolution so the test is hermetic: without this, a local .env
    # carrying real SENTRY_* creds makes _resolve_config fall back to them and
    # the tool reports available=True.
    with patch(
        "integrations.sentry.tools.sentry_search_issues_tool.sentry_config_from_env",
        return_value=None,
    ):
        result = search_sentry_issues(organization_slug="", sentry_token="")
    assert result["available"] is False
    assert result["issues"] == []


def test_run_happy_path() -> None:
    fake_issues = [{"id": "1", "title": "TypeError", "status": "unresolved", "count": 3}]
    with (
        patch(
            "integrations.sentry.tools.sentry_search_issues_tool.list_sentry_issues",
            return_value=fake_issues,
        ),
        patch(
            "integrations.sentry.tools.sentry_search_issues_tool.sentry_config_from_env",
            return_value=None,
        ),
    ):
        result = search_sentry_issues(
            organization_slug="my-org",
            sentry_token="tok_test",
            query="TypeError",
        )
    assert result["available"] is True
    assert len(result["issues"]) == 1
    assert result["query"] == "TypeError"
    assert result["issues_total"] == 1
    assert result["digest"]["issue_count"] == 1
    assert result["digest"]["structural_clusters"]
    assert result["digest"]["top_issues"][0]["title"] == "TypeError"


def test_run_empty_issues() -> None:
    with (
        patch(
            "integrations.sentry.tools.sentry_search_issues_tool.list_sentry_issues",
            return_value=[],
        ),
        patch(
            "integrations.sentry.tools.sentry_search_issues_tool.sentry_config_from_env",
            return_value=None,
        ),
    ):
        result = search_sentry_issues(organization_slug="my-org", sentry_token="tok_test")
    assert result["available"] is True
    assert result["issues"] == []


@pytest.mark.integration
def test_live_env_config_searches_sentry_windows_issues() -> None:
    if not os.getenv("SENTRY_ORG_SLUG") or not os.getenv("SENTRY_AUTH_TOKEN"):
        pytest.skip("SENTRY_ORG_SLUG and SENTRY_AUTH_TOKEN are required for live Sentry search")

    result = search_sentry_issues(
        organization_slug="",
        sentry_token="",
        query="windows",
        limit=5,
    )

    assert result["available"] is True
    assert result["source"] == "sentry"
    assert result["query"] == "windows"
    assert isinstance(result["issues"], list)


# --- _sanitize_sentry_query tests ---


def test_sanitize_sentry_query_plain_term() -> None:
    assert _sanitize_sentry_query("TypeError") == "TypeError"


def test_sanitize_sentry_query_multiline_takes_first_line() -> None:
    raw = "TypeError: Cannot read\n  at foo (bar.ts:1)\n  at baz (qux.ts:2)"
    result = _sanitize_sentry_query(raw)
    assert "\n" not in result
    assert result == "TypeError: Cannot read"


def test_sanitize_sentry_query_truncates_long_query() -> None:
    long = "a" * (_MAX_SENTRY_QUERY_LEN + 50)
    result = _sanitize_sentry_query(long)
    assert len(result) == _MAX_SENTRY_QUERY_LEN


def test_sanitize_sentry_query_strips_whitespace() -> None:
    assert _sanitize_sentry_query("  hello world  ") == "hello world"


def test_sanitize_sentry_query_empty_string() -> None:
    assert _sanitize_sentry_query("") == ""


def test_sanitize_sentry_query_or_takes_first_alternative() -> None:
    raw = '"database connection" OR "too many connections" OR "connection refused"'
    assert _sanitize_sentry_query(raw) == '"database connection"'


def test_sentry_query_candidates_splits_or() -> None:
    raw = '"database connection" OR "too many connections" OR "connection refused"'
    assert _sentry_query_candidates(raw) == [
        '"database connection"',
        '"too many connections"',
        '"connection refused"',
    ]


def test_run_returns_structured_error_on_http_400() -> None:
    request = httpx.Request("GET", "https://sentry.io/api/0/organizations/my-org/issues/")
    response = httpx.Response(
        400,
        request=request,
        json={"detail": "Invalid query."},
    )
    err = httpx.HTTPStatusError("bad request", request=request, response=response)

    with (
        patch(
            "integrations.sentry.tools.sentry_search_issues_tool.list_sentry_issues",
            side_effect=err,
        ),
        patch(
            "integrations.sentry.tools.sentry_search_issues_tool.sentry_config_from_env",
            return_value=None,
        ),
    ):
        result = search_sentry_issues(
            organization_slug="my-org",
            sentry_token="tok_test",
            query='"database connection" OR "too many connections"',
            project_slug="python",
        )

    assert result["available"] is False
    assert result["issues"] == []
    assert "HTTP 400" in result["error"]
    assert "Invalid query." in result["error"]
    assert "does not support OR" in result["error"]
    assert "project slug 'python'" in result["error"]


def test_list_sentry_issues_retries_or_segments_on_400() -> None:
    config = SentryConfig(organization_slug="my-org", auth_token="tok", project_slug="python")
    query = '"database connection" OR "too many connections"'
    calls: list[str] = []

    def _fake_request_json(
        _config: SentryConfig,
        _method: str,
        _path: str,
        *,
        params: list[tuple[str, str | int | float | bool | None]] | None = None,
    ) -> list[dict[str, Any]]:
        assert params is not None
        calls.append(str(dict(params)["query"]))
        if len(calls) == 1:
            request = httpx.Request("GET", "https://sentry.io/api/0/organizations/my-org/issues/")
            response = httpx.Response(400, request=request, json={"detail": "Invalid query."})
            raise httpx.HTTPStatusError("bad request", request=request, response=response)
        return [{"id": "1", "title": "too many connections"}]

    with patch("integrations.sentry.client._request_json", side_effect=_fake_request_json):
        issues = list_sentry_issues(config=config, query=query)

    assert calls == ['"database connection"', '"too many connections"']
    assert len(issues) == 1


def test_describe_sentry_api_error_includes_json_detail() -> None:
    request = httpx.Request("GET", "https://sentry.io/api/0/organizations/my-org/issues/")
    response = httpx.Response(400, request=request, json={"detail": "Invalid search query."})
    err = httpx.HTTPStatusError("bad request", request=request, response=response)

    message = describe_sentry_api_error(err, query="windows", project_slug="backend")

    assert "HTTP 400" in message
    assert "Invalid search query." in message
    assert "project slug 'backend'" in message


def test_build_issue_list_params_sanitizes_multiline_query() -> None:
    """_build_issue_list_params must collapse multi-line stack traces so the
    Sentry API does not return a 400 Bad Request."""
    from integrations.sentry import SentryConfig, _build_issue_list_params

    config = SentryConfig(organization_slug="my-org", auth_token="tok")
    multiline_query = "TypeError: Cannot read props\n  at src/foo.ts:10"
    params = dict(_build_issue_list_params(config, limit=10, query=multiline_query))
    assert "\n" not in str(params["query"])
    assert params["query"] == "TypeError: Cannot read props"


# --- limit / statsPeriod resolution tests (under-retrieval fix) ---


def test_clamp_issue_limit_caps_at_sentry_page_size() -> None:
    from integrations.sentry import _MAX_SENTRY_PAGE_SIZE, _clamp_issue_limit

    assert _clamp_issue_limit(1000) == _MAX_SENTRY_PAGE_SIZE
    assert _clamp_issue_limit(_MAX_SENTRY_PAGE_SIZE) == _MAX_SENTRY_PAGE_SIZE


def test_clamp_issue_limit_floors_at_one() -> None:
    from integrations.sentry import _clamp_issue_limit

    assert _clamp_issue_limit(0) == 1
    assert _clamp_issue_limit(-5) == 1


def test_clamp_issue_limit_defaults_on_bad_input() -> None:
    from integrations.sentry import DEFAULT_SENTRY_ISSUE_LIMIT, _clamp_issue_limit

    assert _clamp_issue_limit(None) == DEFAULT_SENTRY_ISSUE_LIMIT
    assert _clamp_issue_limit("not-an-int") == DEFAULT_SENTRY_ISSUE_LIMIT  # type: ignore[arg-type]


def test_default_issue_limit_is_full_sentry_page() -> None:
    """Regression: a tiny default limit was why searches "only found one issue"."""
    from integrations.sentry import _MAX_SENTRY_PAGE_SIZE, DEFAULT_SENTRY_ISSUE_LIMIT

    assert DEFAULT_SENTRY_ISSUE_LIMIT == _MAX_SENTRY_PAGE_SIZE


def test_build_issue_list_params_clamps_limit_into_page_range() -> None:
    from integrations.sentry import (
        _MAX_SENTRY_PAGE_SIZE,
        SentryConfig,
        _build_issue_list_params,
    )

    config = SentryConfig(organization_slug="my-org", auth_token="tok")
    params = dict(_build_issue_list_params(config, limit=5000, query=""))
    assert params["limit"] == str(_MAX_SENTRY_PAGE_SIZE)


def test_build_issue_list_params_uses_default_stats_period() -> None:
    from integrations.sentry import (
        DEFAULT_SENTRY_STATS_PERIOD,
        SentryConfig,
        _build_issue_list_params,
    )

    config = SentryConfig(organization_slug="my-org", auth_token="tok")
    params = dict(_build_issue_list_params(config, limit=10, query=""))
    assert params["statsPeriod"] == DEFAULT_SENTRY_STATS_PERIOD


def test_build_issue_list_params_explicit_stats_period_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations.sentry import SentryConfig, _build_issue_list_params

    monkeypatch.setenv("SENTRY_STATS_PERIOD", "7d")
    config = SentryConfig(organization_slug="my-org", auth_token="tok")
    params = dict(_build_issue_list_params(config, limit=10, query="", stats_period="14d"))
    assert params["statsPeriod"] == "14d"


def test_build_issue_list_params_stats_period_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations.sentry import SentryConfig, _build_issue_list_params

    monkeypatch.setenv("SENTRY_STATS_PERIOD", "30d")
    config = SentryConfig(organization_slug="my-org", auth_token="tok")
    params = dict(_build_issue_list_params(config, limit=10, query=""))
    assert params["statsPeriod"] == "30d"


def test_validate_sentry_config_reports_recent_issue_count() -> None:
    """Verify reports a meaningful recent issue count over the 7-day window."""
    from integrations.sentry import SentryConfig, validate_sentry_config

    config = SentryConfig(organization_slug="my-org", auth_token="tok")
    captured: dict[str, object] = {}

    def _fake_list(**kwargs: object) -> list[dict]:
        captured.update(kwargs)
        return [{"id": str(i)} for i in range(30)]

    with patch("integrations.sentry.list_sentry_issues", side_effect=_fake_list):
        result = validate_sentry_config(config)

    assert result.ok is True
    assert result.issue_count == 30
    assert "30 issue(s) in the last 7 days" in result.detail
    assert captured["stats_period"] == "7d"


def test_validate_sentry_config_saturated_count_uses_plus() -> None:
    """When the page saturates, the count is shown as ``N+`` (honest about
    the page-size cap rather than implying an exact total)."""
    from integrations.sentry import (
        _MAX_SENTRY_PAGE_SIZE,
        SentryConfig,
        validate_sentry_config,
    )

    config = SentryConfig(organization_slug="my-org", auth_token="tok")
    with patch(
        "integrations.sentry.list_sentry_issues",
        return_value=[{"id": str(i)} for i in range(_MAX_SENTRY_PAGE_SIZE)],
    ):
        result = validate_sentry_config(config)

    assert f"{_MAX_SENTRY_PAGE_SIZE}+ issue(s)" in result.detail


def test_validate_sentry_config_reports_bad_token() -> None:
    from integrations.sentry import SentryConfig, validate_sentry_config

    config = SentryConfig(organization_slug="my-org", auth_token="bad-token")
    request = httpx.Request("GET", "https://sentry.io/api/0/organizations/my-org/issues/")
    response = httpx.Response(
        HTTPStatus.UNAUTHORIZED,
        request=request,
        text="Invalid token.",
    )
    error = httpx.HTTPStatusError("unauthorized", request=request, response=response)

    with patch("integrations.sentry.client._request_json", side_effect=error):
        result = validate_sentry_config(config)

    assert result.ok is False
    assert result.detail == "Sentry validation failed: Invalid token."


def test_search_tool_default_limit_is_full_page() -> None:
    from integrations.sentry import DEFAULT_SENTRY_ISSUE_LIMIT
    from integrations.sentry.tools.sentry_search_issues_tool import _search_issues_extract_params

    sources = {
        "sentry": {
            "organization_slug": "my-org",
            "sentry_token": "tok",
        }
    }
    params = _search_issues_extract_params(sources)
    assert params["limit"] == DEFAULT_SENTRY_ISSUE_LIMIT


def test_search_tool_forwards_limit_and_period_to_client() -> None:
    captured: dict[str, object] = {}

    def _fake_list(**kwargs: object) -> list[dict]:
        captured.update(kwargs)
        return []

    with (
        patch(
            "integrations.sentry.tools.sentry_search_issues_tool.list_sentry_issues",
            side_effect=_fake_list,
        ),
        patch(
            "integrations.sentry.tools.sentry_search_issues_tool.sentry_config_from_env",
            return_value=None,
        ),
    ):
        search_sentry_issues(
            organization_slug="my-org",
            sentry_token="tok",
            limit=50,
            stats_period="14d",
        )

    assert captured["limit"] == 50
    assert captured["stats_period"] == "14d"


class TestMapSearchSentryIssues:
    def test_records_plain_count_when_under_limit(self) -> None:
        from integrations.sentry import DEFAULT_SENTRY_ISSUE_LIMIT

        evidence: dict[str, Any] = {}

        _map_search_sentry_issues(
            evidence,
            {
                "available": True,
                "query": "TypeError",
                "issues_total": 3,
                "issues": [{"id": "1"}, {"id": "2"}, {"id": "3"}],
            },
            {"limit": DEFAULT_SENTRY_ISSUE_LIMIT},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "search_sentry_issues"
        assert entries[0]["summary"] == "3 issue(s) for query 'TypeError'"

    def test_records_saturated_count_with_plus(self) -> None:
        evidence: dict[str, Any] = {}

        _map_search_sentry_issues(
            evidence,
            {
                "available": True,
                "query": "TypeError",
                "issues_total": 10,
                "issues": [{"id": str(i)} for i in range(10)],
            },
            {"limit": 10},
        )

        assert evidence["catalog_entries"][0]["summary"] == "10+ issue(s) for query 'TypeError'"

    def test_sanitizes_long_multiline_query(self) -> None:
        """Regression: the raw query can be a full multi-line stack trace —
        collapse and cap it so it can't produce a malformed report line."""
        evidence: dict[str, Any] = {}
        long_query = "TypeError: boom\n  at foo (bar.ts:1)\n" + "x" * 300

        _map_search_sentry_issues(
            evidence,
            {"available": True, "query": long_query, "issues_total": 1, "issues": [{"id": "1"}]},
            {"limit": 100},
        )

        summary = evidence["catalog_entries"][0]["summary"]
        assert "\n" not in summary
        assert len(summary) < len(long_query)

    def test_records_nothing_when_no_issues(self) -> None:
        evidence: dict[str, Any] = {}

        _map_search_sentry_issues(
            evidence, {"available": True, "query": "x", "issues_total": 0, "issues": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_search_sentry_issues(evidence, {"available": False, "error": "not configured"}, {})

        assert "catalog_entries" not in evidence

"""Tests for BitbucketSearchCodeTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import patch

import pytest

from integrations.bitbucket.tools.bitbucket_search_code_tool import (
    search_bitbucket_code,
)
from integrations.bitbucket.tools.bitbucket_search_code_tool._evidence import (
    map_search_bitbucket_code as _map_search_bitbucket_code,
)
from tests.tools.conftest import BaseToolContract


def _registered_tool() -> Any:
    return cast(Any, search_bitbucket_code).__opensre_registered_tool__


class TestBitbucketSearchCodeToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool()


@pytest.mark.parametrize(
    "sources,expected",
    [
        (
            {
                "bitbucket": {
                    "connection_verified": True,
                    "workspace": "acme",
                    "username": "bb-user",
                    "app_password": "bb-pass",
                }
            },
            True,
        ),
        ({"bitbucket": {"connection_verified": True, "workspace": "acme"}}, False),
        ({"bitbucket": {"connection_verified": True, "username": "bb-user"}}, False),
        ({"bitbucket": {"connection_verified": True, "app_password": "bb-pass"}}, False),
        (
            {"bitbucket": {"workspace": "acme", "username": "bb-user", "app_password": "bb-pass"}},
            False,
        ),
        ({}, False),
    ],
)
def test_is_available_requires_credentials(sources: dict[str, dict], expected: bool) -> None:
    rt = _registered_tool()
    assert rt.is_available(sources) is expected


def test_extract_params_maps_fields() -> None:
    rt = _registered_tool()
    params = rt.extract_params(
        {
            "bitbucket": {
                "query": "error OR exception",
                "repo_slug": "backend-service",
                "workspace": "acme",
                "username": "bb-user",
                "app_password": "bb-pass",
                "base_url": "https://api.bitbucket.org/2.0",
                "max_results": 50,
                "integration_id": "bb-main",
            }
        }
    )

    assert params["query"] == "error OR exception"
    assert params["repo_slug"] == "backend-service"
    assert params["workspace"] == "acme"
    assert params["username"] == "bb-user"
    assert params["app_password"] == "bb-pass"
    assert params["base_url"] == "https://api.bitbucket.org/2.0"
    assert params["max_results"] == 50
    assert params["integration_id"] == "bb-main"
    assert params["limit"] == 20


def test_run_returns_unavailable_without_credentials() -> None:
    with patch(
        "integrations.bitbucket.tools.bitbucket_search_code_tool.bitbucket_config_from_env",
        return_value=None,
    ):
        result = search_bitbucket_code(query="error OR exception")

    assert result == {
        "source": "bitbucket",
        "available": False,
        "error": "Bitbucket integration is not configured.",
        "failure_state": "missing_config",
        "results": [],
    }


def test_run_happy_path() -> None:
    mock_result: dict[str, Any] = {
        "source": "bitbucket",
        "available": True,
        "query": "error OR exception",
        "total_returned": 1,
        "results": [
            {
                "path": "src/main.py",
                "repo": "acme/backend-service",
                "content_matches": 2,
            }
        ],
    }

    with patch(
        "integrations.bitbucket.tools.bitbucket_search_code_tool.search_code",
        return_value=mock_result,
    ) as mocked_search_code:
        result = search_bitbucket_code(
            query="error OR exception",
            workspace="acme",
            username="bb-user",
            app_password="bb-pass",
            repo_slug="backend-service",
            limit=5,
        )

    assert result == mock_result
    mocked_search_code.assert_called_once()
    config = mocked_search_code.call_args.args[0]
    assert config.workspace == "acme"
    assert config.username == "bb-user"
    assert config.app_password == "bb-pass"
    assert mocked_search_code.call_args.kwargs == {
        "query": "error OR exception",
        "repo_slug": "backend-service",
        "limit": 5,
    }


class TestMapSearchBitbucketCode:
    def test_records_entry_with_query(self) -> None:
        evidence: dict[str, Any] = {}

        _map_search_bitbucket_code(
            evidence,
            {
                "available": True,
                "query": "error OR exception",
                "total_returned": 1,
                "effective_limit": 20,
                "results": [{"path": "src/main.py"}],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "search_bitbucket_code"
        assert entries[0]["summary"] == "1 match(es) for 'error OR exception'"

    def test_qualifies_count_when_page_is_saturated(self) -> None:
        evidence: dict[str, Any] = {}

        _map_search_bitbucket_code(
            evidence,
            {
                "available": True,
                "query": "error",
                "total_returned": 20,
                "effective_limit": 20,
                "results": [{"path": "src/main.py"}],
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"].startswith("20+ match(es)")

    def test_strips_newlines_from_query(self) -> None:
        evidence: dict[str, Any] = {}

        _map_search_bitbucket_code(
            evidence,
            {
                "available": True,
                "query": "error\nOR\nexception",
                "total_returned": 1,
                "results": [{"path": "src/main.py"}],
            },
            {},
        )

        assert "\n" not in evidence["catalog_entries"][0]["summary"]

    def test_strips_carriage_returns_from_query(self) -> None:
        """Regression: a query with bare \\r or \\r\\n line endings must not
        leave a literal carriage return in the report summary."""
        evidence: dict[str, Any] = {}

        _map_search_bitbucket_code(
            evidence,
            {
                "available": True,
                "query": "error\r\nOR\rexception",
                "total_returned": 1,
                "results": [{"path": "src/main.py"}],
            },
            {},
        )

        assert "\r" not in evidence["catalog_entries"][0]["summary"]

    def test_records_nothing_when_no_results(self) -> None:
        evidence: dict[str, Any] = {}

        _map_search_bitbucket_code(
            evidence, {"available": True, "total_returned": 0, "results": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_search_bitbucket_code(evidence, {"available": False, "error": "not configured"}, {})

        assert "catalog_entries" not in evidence

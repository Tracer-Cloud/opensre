"""Dedicated unit tests for SnowflakeQueryHistoryTool.

Covers contract metadata, source-availability gating, parameter extraction,
SQL limit enforcement, account/token validation, row normalization across the
two response shapes the Snowflake SQL API returns, and HTTP failure handling.
"""

from __future__ import annotations

from typing import Any

import pytest

from integrations.snowflake.tools.snowflake_query_history_tool import query_snowflake_history
from tests.tools.conftest import BaseToolContract, MockHttpxResponse

# ---------------------------------------------------------------------------
# Contract — metadata, is_available, extract_params surface
# ---------------------------------------------------------------------------


class TestSnowflakeQueryHistoryToolContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return query_snowflake_history.__opensre_registered_tool__


def _rt() -> Any:
    return query_snowflake_history.__opensre_registered_tool__


def test_is_available_true_when_token_account_and_verified() -> None:
    sources = {
        "snowflake": {
            "connection_verified": True,
            "account_identifier": "xy12345.us-east-1",
            "token": "sf-token",
        }
    }
    assert _rt().is_available(sources) is True


def test_is_available_false_when_connection_not_verified() -> None:
    sources = {
        "snowflake": {
            "connection_verified": False,
            "account_identifier": "xy12345.us-east-1",
            "token": "sf-token",
        }
    }
    assert _rt().is_available(sources) is False


def test_is_available_false_when_token_missing() -> None:
    sources = {
        "snowflake": {
            "connection_verified": True,
            "account_identifier": "xy12345.us-east-1",
            "token": "   ",
        }
    }
    assert _rt().is_available(sources) is False


def test_is_available_false_when_account_identifier_missing() -> None:
    sources = {
        "snowflake": {
            "connection_verified": True,
            "account_identifier": "",
            "token": "sf-token",
        }
    }
    assert _rt().is_available(sources) is False


def test_is_available_false_when_no_snowflake_source() -> None:
    assert _rt().is_available({}) is False


def test_extract_params_strips_and_defaults_max_results() -> None:
    sources = {
        "snowflake": {
            "account_identifier": "  xy12345.us-east-1  ",
            "user": " sf-user ",
            "password": " sf-pass ",
            "token": "  sf-token  ",
            "warehouse": "  WH_X  ",
            "role": " ANALYST ",
            "database": " ANALYTICS ",
            "schema": " PUBLIC ",
            "query": " SELECT 1 ",
            "integration_id": " sf-1 ",
        }
    }
    params = _rt().extract_params(sources)
    assert params["account_identifier"] == "xy12345.us-east-1"
    assert params["user"] == "sf-user"
    assert params["password"] == "sf-pass"
    assert params["token"] == "sf-token"
    assert params["warehouse"] == "WH_X"
    assert params["role"] == "ANALYST"
    assert params["database"] == "ANALYTICS"
    assert params["db_schema"] == "PUBLIC"
    assert params["query"] == "SELECT 1"
    assert params["integration_id"] == "sf-1"
    # Default max_results when key absent
    assert params["max_results"] == 50
    # The tool always extracts a fixed limit of 50; bounding happens at run time
    assert params["limit"] == 50


def test_extract_params_uses_default_max_results_when_zero_or_missing() -> None:
    # max_results explicitly 0 should fall back to the default (truthy guard)
    sources = {"snowflake": {"account_identifier": "x", "token": "t", "max_results": 0}}
    assert _rt().extract_params(sources)["max_results"] == 50


# ---------------------------------------------------------------------------
# Validation guards — missing config / required fields
# ---------------------------------------------------------------------------


def test_returns_missing_account_when_account_identifier_blank() -> None:
    result = query_snowflake_history(account_identifier="   ", token="sf-token")
    assert result["available"] is False
    assert result["error"] == "Missing account identifier."
    assert result["rows"] == []


def test_returns_missing_token_when_token_blank() -> None:
    result = query_snowflake_history(account_identifier="xy12345.us-east-1", token="   ")
    assert result["available"] is False
    assert result["error"] == "Missing Snowflake token."
    assert result["rows"] == []


# ---------------------------------------------------------------------------
# Request shaping — SQL LIMIT enforcement, payload contents, endpoint
# ---------------------------------------------------------------------------


def test_default_query_used_when_query_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float):
        captured["url"] = url
        captured["statement"] = json["statement"]
        captured["headers"] = headers
        return MockHttpxResponse({"data": []})

    monkeypatch.setattr(
        "integrations.snowflake.tools.snowflake_query_history_tool.tool.httpx.post", _fake_post
    )

    result = query_snowflake_history(
        account_identifier="xy12345.us-east-1",
        token="sf-token",
        query="",
        max_results=10,
    )
    assert result["available"] is True
    assert "INFORMATION_SCHEMA.QUERY_HISTORY" in captured["statement"]
    # Default query embeds RESULT_LIMIT directly, not a trailing LIMIT clause
    assert "RESULT_LIMIT => 10" in captured["statement"]
    # Endpoint is correctly templated from the account identifier
    assert captured["url"] == "https://xy12345.us-east-1.snowflakecomputing.com/api/v2/statements"
    assert captured["headers"]["Authorization"] == "Bearer sf-token"
    assert captured["headers"]["Content-Type"] == "application/json"


def test_user_query_gets_limit_appended_if_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float):
        captured["statement"] = json["statement"]
        return MockHttpxResponse({"data": []})

    monkeypatch.setattr(
        "integrations.snowflake.tools.snowflake_query_history_tool.tool.httpx.post", _fake_post
    )

    query_snowflake_history(
        account_identifier="xy12345.us-east-1",
        token="sf-token",
        query="SELECT * FROM T;",
        limit=1000,  # caller asks for 1000 but max_results caps it
        max_results=7,
    )

    assert captured["statement"].endswith("LIMIT 7")
    assert ";" not in captured["statement"]  # trailing semicolon stripped


def test_user_query_with_existing_limit_is_left_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float):
        captured["statement"] = json["statement"]
        return MockHttpxResponse({"data": []})

    monkeypatch.setattr(
        "integrations.snowflake.tools.snowflake_query_history_tool.tool.httpx.post", _fake_post
    )

    query_snowflake_history(
        account_identifier="xy12345.us-east-1",
        token="sf-token",
        query="SELECT * FROM T LIMIT 3",
        limit=500,
        max_results=50,
    )

    # The ensure_sql_limit helper must not double-stamp the LIMIT clause
    assert captured["statement"].lower().count("limit") == 1
    assert captured["statement"].endswith("LIMIT 3")


def test_optional_session_params_only_included_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float):
        captured["payload"] = json
        return MockHttpxResponse({"data": []})

    monkeypatch.setattr(
        "integrations.snowflake.tools.snowflake_query_history_tool.tool.httpx.post", _fake_post
    )

    query_snowflake_history(
        account_identifier="xy12345.us-east-1",
        token="sf-token",
        warehouse="WH_X",
        role="ANALYST",
        database="DB",
        db_schema="PUBLIC",
        max_results=5,
    )
    payload = captured["payload"]
    assert payload["warehouse"] == "WH_X"
    assert payload["role"] == "ANALYST"
    assert payload["database"] == "DB"
    assert payload["schema"] == "PUBLIC"
    assert "statement" in payload
    assert "timeout" in payload


def test_optional_session_params_omitted_when_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float):
        captured["payload"] = json
        return MockHttpxResponse({"data": []})

    monkeypatch.setattr(
        "integrations.snowflake.tools.snowflake_query_history_tool.tool.httpx.post", _fake_post
    )

    query_snowflake_history(
        account_identifier="xy12345.us-east-1",
        token="sf-token",
        max_results=5,
    )
    payload = captured["payload"]
    for key in ("warehouse", "role", "database", "schema"):
        assert key not in payload


def test_bounded_limit_caps_caller_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caller asks for limit=500 but max_results=6 ⇒ server query and rows capped at 6."""

    def _fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float):
        # Server returns 20 rows — tool must trim to effective_limit (6)
        return MockHttpxResponse({"data": [{"id": idx} for idx in range(20)]})

    monkeypatch.setattr(
        "integrations.snowflake.tools.snowflake_query_history_tool.tool.httpx.post", _fake_post
    )

    result = query_snowflake_history(
        account_identifier="xy12345.us-east-1",
        token="sf-token",
        query="SELECT * FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY())",
        limit=500,
        max_results=6,
    )

    assert result["available"] is True
    assert len(result["rows"]) == 6
    assert result["total_returned"] == 6


# ---------------------------------------------------------------------------
# Row normalization — both API response shapes
# ---------------------------------------------------------------------------


def test_normalizes_dict_rows_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the API returns ``data=[{...}, ...]``, rows are passed through as-is."""

    def _fake_post(*_args: Any, **_kwargs: Any) -> MockHttpxResponse:
        return MockHttpxResponse(
            {
                "data": [
                    {"query_id": "q1", "user_name": "alice"},
                    {"query_id": "q2", "user_name": "bob"},
                ]
            }
        )

    monkeypatch.setattr(
        "integrations.snowflake.tools.snowflake_query_history_tool.tool.httpx.post", _fake_post
    )

    result = query_snowflake_history(
        account_identifier="xy12345.us-east-1",
        token="sf-token",
        max_results=10,
    )
    assert result["rows"] == [
        {"query_id": "q1", "user_name": "alice"},
        {"query_id": "q2", "user_name": "bob"},
    ]


def test_normalizes_tabular_rows_using_result_set_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the API returns lists of values + rowType metadata, rows are zipped to dicts."""

    def _fake_post(*_args: Any, **_kwargs: Any) -> MockHttpxResponse:
        return MockHttpxResponse(
            {
                "data": [
                    ["q1", "alice"],
                    ["q2", "bob"],
                ],
                "resultSetMetaData": {
                    "rowType": [
                        {"name": "query_id"},
                        {"name": "user_name"},
                    ]
                },
            }
        )

    monkeypatch.setattr(
        "integrations.snowflake.tools.snowflake_query_history_tool.tool.httpx.post", _fake_post
    )

    result = query_snowflake_history(
        account_identifier="xy12345.us-east-1",
        token="sf-token",
        max_results=10,
    )
    assert result["rows"] == [
        {"query_id": "q1", "user_name": "alice"},
        {"query_id": "q2", "user_name": "bob"},
    ]


def test_normalizes_to_empty_when_payload_unrecognized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown response shapes degrade gracefully to an empty rows list, not an exception."""

    def _fake_post(*_args: Any, **_kwargs: Any) -> MockHttpxResponse:
        return MockHttpxResponse({"unexpected": "shape"})

    monkeypatch.setattr(
        "integrations.snowflake.tools.snowflake_query_history_tool.tool.httpx.post", _fake_post
    )

    result = query_snowflake_history(
        account_identifier="xy12345.us-east-1",
        token="sf-token",
        max_results=10,
    )
    assert result["available"] is True
    assert result["rows"] == []
    assert result["total_returned"] == 0


# ---------------------------------------------------------------------------
# HTTP failure handling
# ---------------------------------------------------------------------------


def test_returns_unavailable_on_http_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_post(*_args: Any, **_kwargs: Any) -> MockHttpxResponse:
        return MockHttpxResponse(
            {"error": "boom"},
            raise_for_status_error=RuntimeError("HTTP 500 from Snowflake"),
        )

    monkeypatch.setattr(
        "integrations.snowflake.tools.snowflake_query_history_tool.tool.httpx.post", _fake_post
    )

    result = query_snowflake_history(
        account_identifier="xy12345.us-east-1",
        token="sf-token",
        max_results=5,
    )
    assert result["available"] is False
    assert "HTTP 500 from Snowflake" in result["error"]
    assert result["rows"] == []


def test_returns_unavailable_on_request_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_args: Any, **_kwargs: Any) -> None:
        raise ConnectionError("connection refused")

    monkeypatch.setattr(
        "integrations.snowflake.tools.snowflake_query_history_tool.tool.httpx.post", _raise
    )

    result = query_snowflake_history(
        account_identifier="xy12345.us-east-1",
        token="sf-token",
        max_results=5,
    )
    assert result["available"] is False
    assert "connection refused" in result["error"]
    assert result["rows"] == []

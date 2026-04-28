"""Unit tests for the shared SQL tool wrapper helper.

See: https://github.com/Tracer-Cloud/opensre/issues/894
"""

from __future__ import annotations

from app.tools.utils.sql_wrapper import run_sql_tool


def _integration_ok(**kwargs) -> dict:
    return {"available": True, "rows": [{"query": "SELECT 1"}], **kwargs}


def _integration_fail(**kwargs) -> dict:
    return {"available": False, "error": "connection refused"}


# ── Core wrapper behaviour ────────────────────────────────────────────────────

def test_run_sql_tool_returns_integration_result() -> None:
    result = run_sql_tool(_integration_ok)
    assert result["available"] is True
    assert "rows" in result


def test_run_sql_tool_forwards_kwargs() -> None:
    result = run_sql_tool(_integration_ok, host="localhost", port=5432)
    assert result["host"] == "localhost"
    assert result["port"] == 5432


def test_run_sql_tool_no_warning_by_default() -> None:
    result = run_sql_tool(_integration_ok)
    assert "warning" not in result


# ── Warning injection ─────────────────────────────────────────────────────────

def test_run_sql_tool_injects_warning_on_success() -> None:
    result = run_sql_tool(
        _integration_ok,
        warning="Using default database.",
    )
    assert result["warning"] == "Using default database."


def test_run_sql_tool_does_not_inject_warning_on_failure() -> None:
    """Warning must NOT be added when the integration call fails."""
    result = run_sql_tool(
        _integration_fail,
        warning="This should not appear.",
    )
    assert "warning" not in result
    assert result["available"] is False


def test_run_sql_tool_preserves_error_dict_on_failure() -> None:
    result = run_sql_tool(_integration_fail)
    assert result["error"] == "connection refused"


# ── Regression: output keys unchanged ────────────────────────────────────────

def test_run_sql_tool_output_keys_unchanged() -> None:
    """Tool output keys must not be renamed or removed by the wrapper."""
    result = run_sql_tool(_integration_ok)
    expected_keys = {"available", "rows"}
    assert expected_keys.issubset(result.keys())

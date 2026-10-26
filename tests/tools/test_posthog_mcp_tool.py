"""Tests for PostHog MCP function tools."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from integrations.posthog_mcp.tools.posthog_mcp_tool import (
    _resolve_config,
    call_posthog_tool,
    list_posthog_tools,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestPostHogListToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_posthog_tools.__opensre_registered_tool__


class TestPostHogCallToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return call_posthog_tool.__opensre_registered_tool__


_CONNECTION_PARAMS = frozenset(
    {
        "posthog_url",
        "posthog_mode",
        "posthog_token",
        "posthog_command",
        "posthog_args",
        "posthog_organization_id",
        "posthog_project_id",
    }
)


def test_connection_params_are_injected_not_model_supplied() -> None:
    """Regression: the LLM must not be able to supply connection/transport
    settings. Hallucinated values (e.g. mode="mcp" or a base URL without the
    ``/mcp`` path) previously overrode the verified config and broke calls, so
    these fields are injected from the verified integration and hidden from the
    model's tool schema.
    """
    for tool_fn in (list_posthog_tools, call_posthog_tool):
        rt = tool_fn.__opensre_registered_tool__
        assert set(rt.injected_params) >= _CONNECTION_PARAMS, (
            f"{rt.name} must inject connection params, not expose them to the model."
        )
        public_props = set(rt.public_input_schema.get("properties", {}))
        assert public_props.isdisjoint(_CONNECTION_PARAMS), (
            f"{rt.name} leaks connection params {public_props & _CONNECTION_PARAMS} "
            "into the model-facing schema."
        )


def test_call_tool_public_schema_exposes_only_tool_selection() -> None:
    rt = call_posthog_tool.__opensre_registered_tool__
    public_props = set(rt.public_input_schema.get("properties", {}))
    assert public_props == {"tool_name", "arguments"}
    assert rt.public_input_schema.get("required") == ["tool_name"]


def test_list_tool_public_schema_exposes_only_filter_controls() -> None:
    """The model may steer discovery (filter + schema toggle) but must never see
    connection/transport params — those are injected from the verified config.
    """
    rt = list_posthog_tools.__opensre_registered_tool__
    public_props = set(rt.public_input_schema.get("properties", {}))
    assert public_props == {"name_filter", "include_schema"}
    assert public_props.isdisjoint(_CONNECTION_PARAMS)


def test_validate_public_input_rejects_model_supplied_connection_params() -> None:
    rt = call_posthog_tool.__opensre_registered_tool__
    # tool_name only is the valid model-facing shape.
    assert rt.validate_public_input({"tool_name": "query-run"}) is None


def test_tools_available_when_connection_verified() -> None:
    sources = mock_agent_state(
        {
            "posthog_mcp": {
                "connection_verified": True,
                "url": "https://mcp.posthog.com/mcp",
                "mode": "streamable-http",
                "auth_token": "phx_secret",
                "organization_id": "org_123",
                "project_id": "proj_456",
            }
        }
    )
    assert list_posthog_tools.__opensre_registered_tool__.is_available(sources) is True
    assert call_posthog_tool.__opensre_registered_tool__.is_available(sources) is True


def test_tools_unavailable_without_verification() -> None:
    sources = mock_agent_state({"posthog_mcp": {"connection_verified": False}})
    assert list_posthog_tools.__opensre_registered_tool__.is_available(sources) is False


def test_extract_params_maps_source_fields() -> None:
    rt = call_posthog_tool.__opensre_registered_tool__
    params = rt.extract_params(
        {
            "posthog_mcp": {
                "connection_verified": True,
                "url": "https://mcp.posthog.com/mcp",
                "mode": "streamable-http",
                "auth_token": "phx_secret",
                "organization_id": "org_123",
                "project_id": "proj_456",
            }
        }
    )
    assert params["posthog_url"] == "https://mcp.posthog.com/mcp"
    assert params["posthog_mode"] == "streamable-http"
    assert params["posthog_token"] == "phx_secret"
    assert params["posthog_organization_id"] == "org_123"
    assert params["posthog_project_id"] == "proj_456"


def test_call_tool_requires_tool_name() -> None:
    result = call_posthog_tool(
        tool_name="",
        posthog_url="https://mcp.posthog.com/mcp",
        posthog_token="phx_secret",
    )
    assert result["available"] is False
    assert "tool_name is required" in str(result["error"])


def test_call_tool_unconfigured_returns_unavailable(monkeypatch) -> None:
    for var in (
        "POSTHOG_MCP_MODE",
        "POSTHOG_MCP_URL",
        "POSTHOG_MCP_COMMAND",
        "POSTHOG_MCP_AUTH_TOKEN",
        "POSTHOG_MCP_ARGS",
    ):
        monkeypatch.delenv(var, raising=False)
    result = call_posthog_tool(tool_name="query-run")
    assert result["available"] is False
    assert "not configured" in str(result["error"])


def test_normalize_unwraps_nested_execute_sql_query() -> None:
    from integrations.posthog_mcp.tools.posthog_mcp_tool import (
        _normalize_mcp_tool_arguments,
    )

    nested = {"query": {"query": "SELECT 1"}}
    assert _normalize_mcp_tool_arguments("execute-sql", nested) == {"query": "SELECT 1"}
    assert _normalize_mcp_tool_arguments("query-run", nested) == {"query": "SELECT 1"}
    flat = {"query": "SELECT 1"}
    assert _normalize_mcp_tool_arguments("execute-sql", flat) is flat
    other = {"query": {"kind": "HogQLQuery", "query": "SELECT 1"}}
    assert _normalize_mcp_tool_arguments("execute-sql", other) is other


def test_normalize_rewrites_botched_exec_shapes_to_execute_sql() -> None:
    """Live dogfood: list returned only ``exec``; three wrong shapes failed."""
    from integrations.posthog_mcp.tools.posthog_mcp_tool import _normalize_mcp_tool_call

    sql = (
        "SELECT uniqExact(person_id) AS windows_users_last_7d FROM events "
        "WHERE timestamp >= now() - INTERVAL 7 DAY "
        "AND lowerUTF8(toString(properties.$os)) = 'windows'"
    )
    # Shape A — query without command (missing required parameter: command).
    assert _normalize_mcp_tool_call("exec", {"query": sql}) == (
        "execute-sql",
        {"query": sql},
    )
    # Shape B — raw SQL as command (Unknown command: SELECT).
    assert _normalize_mcp_tool_call("exec", {"command": sql}) == (
        "execute-sql",
        {"query": sql},
    )
    # Shape C — structured call fields (Usage: call <tool_name> <json_input>).
    assert _normalize_mcp_tool_call(
        "exec",
        {
            "command": "call",
            "tool": "execute-sql",
            "arguments": {"query": sql},
        },
    ) == ("execute-sql", {"query": sql})
    # Shape D — canonical exec command string.
    assert _normalize_mcp_tool_call(
        "exec",
        {"command": f'call execute-sql {{"query": {json.dumps(sql)}}}'},
    ) == ("execute-sql", {"query": sql})


def test_call_tool_rewrites_exec_query_before_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_call(
        _config: object, tool_name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        captured["tool_name"] = tool_name
        captured["arguments"] = arguments
        return {
            "is_error": False,
            "text": "windows_users_last_7d\n271",
            "structured_content": {},
            "content": [],
            "tool": tool_name,
            "arguments": arguments,
        }

    monkeypatch.setattr(
        "integrations.posthog_mcp.tools.posthog_mcp_tool.call_posthog_mcp_tool",
        _fake_call,
    )
    result = call_posthog_tool(
        tool_name="exec",
        arguments={"query": "SELECT uniqExact(person_id) FROM events"},
        posthog_url="https://mcp.posthog.com/mcp",
        posthog_mode="streamable-http",
        posthog_token="phx_secret",
    )
    assert result["available"] is True
    assert captured["tool_name"] == "execute-sql"
    assert captured["arguments"] == {"query": "SELECT uniqExact(person_id) FROM events"}


def test_call_tool_unwraps_nested_execute_sql_before_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_call(
        _config: object, tool_name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        captured["tool_name"] = tool_name
        captured["arguments"] = arguments
        return {
            "is_error": False,
            "text": "ok",
            "structured_content": {},
            "content": [],
            "tool": tool_name,
            "arguments": arguments,
        }

    monkeypatch.setattr(
        "integrations.posthog_mcp.tools.posthog_mcp_tool.call_posthog_mcp_tool",
        _fake_call,
    )
    result = call_posthog_tool(
        tool_name="execute-sql",
        arguments={"query": {"query": "SELECT count() FROM events"}},
        posthog_url="https://mcp.posthog.com/mcp",
        posthog_mode="streamable-http",
        posthog_token="phx_secret",
    )
    assert result["available"] is True
    assert captured["arguments"] == {"query": "SELECT count() FROM events"}


def test_call_tool_passes_through_result() -> None:
    fake_result = {
        "is_error": False,
        "text": "rows",
        "structured_content": {"results": [1, 2]},
        "content": [],
        "tool": "query-run",
        "arguments": {"query": "SELECT 1"},
    }
    with patch(
        "integrations.posthog_mcp.tools.posthog_mcp_tool.call_posthog_mcp_tool",
        return_value=fake_result,
    ) as mock_invoke:
        result = call_posthog_tool(
            tool_name="query-run",
            arguments={"query": "SELECT 1"},
            posthog_url="https://mcp.posthog.com/mcp",
            posthog_mode="streamable-http",
            posthog_token="phx_secret",
        )
    mock_invoke.assert_called_once()
    assert result["available"] is True
    assert result["source"] == "posthog_mcp"
    # SQL tools compact to results-first so gather truncation keeps the numbers.
    assert result["results"] == [1, 2]
    assert result["text"] == "rows"


def test_call_tool_surfaces_mcp_error() -> None:
    fake_result = {
        "is_error": True,
        "text": "permission denied",
        "tool": "feature-flag-create",
        "arguments": {},
    }
    with patch(
        "integrations.posthog_mcp.tools.posthog_mcp_tool.call_posthog_mcp_tool",
        return_value=fake_result,
    ):
        result = call_posthog_tool(
            tool_name="feature-flag-create",
            posthog_url="https://mcp.posthog.com/mcp",
            posthog_token="phx_secret",
        )
    assert result["available"] is False
    assert "permission denied" in str(result["error"])


@pytest.mark.parametrize("guessed_mode", ["default", "mcp", "http", "bogus", ""])
def test_resolve_config_recovers_from_guessed_mode(guessed_mode: str) -> None:
    """The planner often guesses an invalid transport; fall back to HTTP."""
    config = _resolve_config(
        posthog_url="https://mcp.posthog.com/mcp",
        posthog_mode=guessed_mode,
        posthog_token="phx_secret",
    )
    assert config is not None
    assert config.mode == "streamable-http"
    assert config.url == "https://mcp.posthog.com/mcp"


def test_resolve_config_preserves_project_scope_from_injected_params() -> None:
    config = _resolve_config(
        posthog_url="https://mcp.posthog.com/mcp",
        posthog_mode="streamable-http",
        posthog_token="phx_secret",
        posthog_organization_id="org_123",
        posthog_project_id="proj_456",
    )

    assert config is not None
    assert config.organization_id == "org_123"
    assert config.project_id == "proj_456"
    assert config.request_headers["x-posthog-organization-id"] == "org_123"
    assert config.request_headers["x-posthog-project-id"] == "proj_456"


def test_resolve_config_stdio_without_command_falls_back_to_http() -> None:
    """A 'stdio' request with only a URL must not build a broken stdio config."""
    config = _resolve_config(
        posthog_url="https://mcp.posthog.com/mcp",
        posthog_mode="stdio",
        posthog_token="phx_secret",
        posthog_command="",
    )
    assert config is not None
    assert config.mode == "streamable-http"


def test_resolve_config_keeps_explicit_stdio_with_command() -> None:
    config = _resolve_config(
        posthog_url="",
        posthog_mode="stdio",
        posthog_token="",
        posthog_command="npx",
        posthog_args=["-y", "@posthog/mcp-server"],
    )
    assert config is not None
    assert config.mode == "stdio"
    assert config.command == "npx"


def test_list_tools_returns_compact_summaries_without_schema() -> None:
    """Default listing drops input_schema and truncates descriptions so the
    payload cannot overflow the agent's context budget.
    """
    fake_tools = [
        {"name": "execute-sql", "description": "Run HogQL", "input_schema": {"a": 1}},
    ]
    with patch(
        "integrations.posthog_mcp.tools.posthog_mcp_tool.list_posthog_mcp_tools",
        return_value=fake_tools,
    ):
        result = list_posthog_tools(
            posthog_url="https://mcp.posthog.com/mcp",
            posthog_mode="streamable-http",
            posthog_token="phx_secret",
        )
    assert result["available"] is True
    assert result["transport"] == "streamable-http"
    assert result["total_tools"] == 1
    assert result["returned_tools"] == 1
    assert result["tools"] == [{"name": "execute-sql", "description": "Run HogQL"}]
    assert "input_schema" not in result["tools"][0]


def _many_fake_tools(count: int) -> list[dict[str, object]]:
    return [
        {"name": f"tool-{i:03d}", "description": f"desc {i}", "input_schema": {"i": i}}
        for i in range(count)
    ]


def test_list_tools_caps_large_listing_and_notes_truncation() -> None:
    with patch(
        "integrations.posthog_mcp.tools.posthog_mcp_tool.list_posthog_mcp_tools",
        return_value=_many_fake_tools(244),
    ):
        result = list_posthog_tools(
            posthog_url="https://mcp.posthog.com/mcp",
            posthog_token="phx_secret",
        )
    assert result["total_tools"] == 244
    # Bounded well under the full set to keep the payload small.
    assert result["returned_tools"] <= 80
    assert len(result["tools"]) == result["returned_tools"]
    assert "name_filter" in str(result.get("notes", ""))


def test_list_tools_filters_by_name_or_description() -> None:
    fake_tools = [
        {"name": "execute-sql", "description": "Run HogQL", "input_schema": {}},
        {"name": "feature-flag-get-all", "description": "List flags", "input_schema": {}},
        {"name": "query-trends", "description": "Trend query over events", "input_schema": {}},
    ]
    with patch(
        "integrations.posthog_mcp.tools.posthog_mcp_tool.list_posthog_mcp_tools",
        return_value=fake_tools,
    ):
        result = list_posthog_tools(
            name_filter="events query sql",
            posthog_url="https://mcp.posthog.com/mcp",
            posthog_token="phx_secret",
        )
    names = {t["name"] for t in result["tools"]}
    assert names == {"execute-sql", "query-trends"}
    assert result["matched_tools"] == 2
    assert result["name_filter"] == "events query sql"


def test_list_tools_includes_schema_only_for_narrow_results() -> None:
    fake_tools = [
        {"name": "execute-sql", "description": "Run HogQL", "input_schema": {"q": "str"}},
        {"name": "query-trends", "description": "Trends", "input_schema": {"t": "str"}},
    ]
    with patch(
        "integrations.posthog_mcp.tools.posthog_mcp_tool.list_posthog_mcp_tools",
        return_value=fake_tools,
    ):
        result = list_posthog_tools(
            name_filter="execute-sql",
            include_schema=True,
            posthog_url="https://mcp.posthog.com/mcp",
            posthog_token="phx_secret",
        )
    assert result["returned_tools"] == 1
    assert result["tools"][0]["input_schema"] == {"q": "str"}


def test_list_tools_omits_schema_when_too_many_match() -> None:
    with patch(
        "integrations.posthog_mcp.tools.posthog_mcp_tool.list_posthog_mcp_tools",
        return_value=_many_fake_tools(50),
    ):
        result = list_posthog_tools(
            include_schema=True,
            posthog_url="https://mcp.posthog.com/mcp",
            posthog_token="phx_secret",
        )
    assert all("input_schema" not in t for t in result["tools"])
    assert "input_schema omitted" in str(result.get("notes", ""))


def test_list_tools_truncates_long_descriptions() -> None:
    long_desc = "x" * 500
    with patch(
        "integrations.posthog_mcp.tools.posthog_mcp_tool.list_posthog_mcp_tools",
        return_value=[{"name": "execute-sql", "description": long_desc, "input_schema": {}}],
    ):
        result = list_posthog_tools(
            posthog_url="https://mcp.posthog.com/mcp",
            posthog_token="phx_secret",
        )
    description = result["tools"][0]["description"]
    assert len(description) <= 160
    assert description.endswith("\u2026")


def test_query_values_are_first_in_normalized_result() -> None:
    """Row values stay at the front of the payload so later truncation cannot drop them."""
    from integrations.posthog_mcp.tools.posthog_mcp_tool import _normalize_tool_result

    long_sql = "SELECT countIf(timestamp >= now() - INTERVAL 7 DAY) AS current " + (
        "-- padding to blow the per-tool budget\n" * 200
    )
    normalized = _normalize_tool_result(
        {
            "is_error": False,
            "tool": "execute-sql",
            "arguments": {"query": long_sql},
            "text": "investigations_started=4271",
            "structured_content": {"results": [[4271, 3900]]},
            "content": [{"type": "text", "text": "x" * 8000}],
        }
    )

    assert list(normalized.keys())[0] == "results"
    assert normalized["results"] == [[4271, 3900]]
    assert "arguments" not in normalized
    assert "content" not in normalized

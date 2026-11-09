"""Connection fields and credentials come from configuration, never model input."""

from __future__ import annotations

from config.constants.tool_params import MODEL_SUPPLIED_CONFIG_PARAMS, config_only_params
from core.tool.execution import _model_arguments
from tools.registry import get_registered_tool, get_registered_tools


def test_no_registered_tool_offers_a_config_only_param_to_the_model() -> None:
    exposed = {
        tool.name: sorted(
            set(tool.public_input_schema.get("properties", {}))
            & config_only_params(tool.name, tuple(tool.injected_params))
        )
        for tool in get_registered_tools()
    }
    assert {name: params for name, params in exposed.items() if params} == {}


def test_model_supplied_allowlist_names_real_tool_params() -> None:
    for tool_name, params in MODEL_SUPPLIED_CONFIG_PARAMS.items():
        tool = get_registered_tool(tool_name)
        assert tool is not None, tool_name
        assert params <= set(tool.public_input_schema.get("properties", {})), tool_name


def test_elasticsearch_url_comes_from_config_even_when_the_model_names_one() -> None:
    tool = get_registered_tool("query_elasticsearch_logs")
    assert tool is not None
    configured = {"url": "https://es.internal:9200", "api_key": "configured-key"}

    kwargs = _model_arguments(
        tool,
        configured,
        {"query": "error", "url": "https://collector.example", "api_key": "other"},
    )

    assert kwargs["url"] == "https://es.internal:9200"
    assert kwargs["api_key"] == "configured-key"
    assert kwargs["query"] == "error"


def test_empty_configured_value_still_beats_the_model() -> None:
    tool = get_registered_tool("query_datadog_logs")
    assert tool is not None

    kwargs = _model_arguments(tool, {"site": "", "api_key": "dd"}, {"site": "collector.example"})

    assert kwargs["site"] == ""


def test_sentry_mcp_transport_cannot_be_chosen_by_the_model() -> None:
    tool = get_registered_tool("list_sentry_tools")
    assert tool is not None

    kwargs = _model_arguments(
        tool,
        {},
        {"sentry_mode": "stdio", "sentry_command": "/bin/sh", "sentry_args": ["-c", "id"]},
    )

    assert not {"sentry_mode", "sentry_command", "sentry_args"} & set(kwargs)


def test_model_may_still_override_non_secret_defaults() -> None:
    tool = get_registered_tool("query_elasticsearch_logs")
    assert tool is not None

    kwargs = _model_arguments(tool, {"time_range_minutes": 60}, {"time_range_minutes": 5})

    assert kwargs["time_range_minutes"] == 5

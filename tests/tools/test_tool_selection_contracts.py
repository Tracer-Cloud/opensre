from __future__ import annotations

import pytest

from core.domain.types.tools import ToolSurface
from tools.registry import get_registered_tool_map, get_registered_tools

# Parameter names that are supplied from resolved integration config (auth
# secrets and connection/account identities) and that the model can NEVER
# legitimately provide. Such a param MUST be declared in a tool's
# ``injected_params`` so it is stripped from the model-facing schema and
# auto-injected at run time. If one stays in the model-facing ``required`` list,
# the LLM is asked for a value it cannot know, so it silently never calls the
# tool — the conversational assistant then answers from prose instead of live
# data. See ``tools/SentrySearchIssuesTool``.
CREDENTIAL_PARAM_NAMES = frozenset(
    {
        # auth secrets
        "api_key",
        "app_key",
        "api_token",
        "auth_token",
        "sentry_token",
        "access_token",
        "api_private_key",
        "api_public_key",
        "password",
        "secret",
        "secret_key",
        "client_secret",
        "connection_string",
        "grafana_api_key",
        "grafana_password",
        "token",
        # connection / account identity (config-only)
        "base_url",
        "site",
        "host",
        "server",
        "endpoint",
        "url",
        "api_url",
        "project_url",
        "workspace_id",
        "account_identifier",
        "bootstrap_servers",
        "query_endpoint",
        "organization_slug",
        "role_arn",
        "credentials_file",
        "grafana_endpoint",
        "grafana_username",
        "username",
        "email",
        "credentials",
        "external_id",
    }
)

# (tool_name, param) pairs where a credential-NAMED parameter is genuinely a
# model-supplied target, not a credential, and is intentionally exposed. Keep
# this list tiny and justified; each entry is a deliberate exception.
MODEL_SUPPLIED_CREDENTIAL_PARAMS = frozenset(
    {
        # CloudTrail filters events by an IAM principal name; ``username`` here is
        # the forensic search target, not an auth credential.
        ("lookup_cloudtrail_events", "username"),
    }
)


def _all_registered_tools() -> dict[str, object]:
    tools: dict[str, object] = {}
    for surface in (ToolSurface.CHAT,):
        for tool in get_registered_tools(surface):
            tools[tool.name] = tool
    return tools


def test_cross_platform_send_tools_are_not_chat_surfaced() -> None:
    # On a gateway chat turn the reply is delivered by the sink, so exposing the
    # send tools lets the agent pick the wrong platform (e.g. telegram_send_message
    # on a Slack turn). They stay on the action surface only.
    chat_tool_names = {tool.name for tool in get_registered_tools("chat")}
    assert "telegram_send_message" not in chat_tool_names
    assert "slack_send_message" not in chat_tool_names
    assert "rocketchat_send_message" not in chat_tool_names


def test_no_tool_requires_a_credential_in_its_model_facing_schema() -> None:
    """Credentials must be injected from config, never required of the model.

    Regression guard for the whole tool registry: a tool whose model-facing
    ``required`` list contains an auth secret or connection identity can never be
    invoked by the planner/assistant, because the model cannot supply that value.
    The fix is always to add the param to the tool's ``injected_params``.
    """
    offenders: dict[str, list[str]] = {}
    for name, tool in sorted(_all_registered_tools().items()):
        schema = tool.public_input_schema  # type: ignore[attr-defined]
        required = set(schema.get("required", []) or [])
        leaked = sorted(
            param
            for param in (required & CREDENTIAL_PARAM_NAMES)
            if (name, param) not in MODEL_SUPPLIED_CREDENTIAL_PARAMS
        )
        if leaked:
            offenders[name] = leaked

    assert not offenders, (
        "These tools require a config-injected credential in their model-facing "
        "schema; add each listed param to the tool's injected_params:\n"
        + "\n".join(f"  - {name}: {params}" for name, params in offenders.items())
    )


@pytest.mark.parametrize(
    "tool_name",
    [
        "search_sentry_issues",
        "jira_search_issues",
        "get_mysql_server_status",
        "alertmanager_alerts",
        "query_azure_monitor_logs",
        "describe_eks_cluster",
    ],
)
def test_representative_tools_hide_credentials_but_keep_targets(tool_name: str) -> None:
    """Spot-check that fixed tools strip credentials yet keep model-facing args."""
    tool = _all_registered_tools()[tool_name]
    props = set(tool.public_input_schema.get("properties", {}).keys())  # type: ignore[attr-defined]
    required = set(tool.public_input_schema.get("required", []))  # type: ignore[attr-defined]
    assert CREDENTIAL_PARAM_NAMES.isdisjoint(required)
    # The injected credentials are also gone from the visible properties.
    assert (
        set(tool.injected_params) & CREDENTIAL_PARAM_NAMES
    )  # declares some creds  # type: ignore[attr-defined]
    assert set(tool.injected_params).isdisjoint(props)  # type: ignore[attr-defined]


def test_rds_performance_family_uses_rds_and_postgresql_contracts() -> None:
    tool_map = get_registered_tool_map()

    rds_tool = tool_map["describe_rds_instance"]
    pg_tool = tool_map["get_postgresql_slow_queries"]

    assert rds_tool.source == "rds"
    assert "db_instance_identifier" in set(rds_tool.public_input_schema.get("required", []))

    assert pg_tool.source == "postgresql"
    pg_props = set(pg_tool.public_input_schema.get("properties", {}).keys())
    # ``threshold_ms`` is a real model-supplied parameter and stays visible.
    assert "threshold_ms" in pg_props
    # ``host`` is a config-injected connection identity and must NOT be exposed
    # to the model (it is supplied from the resolved integration, not the LLM).
    assert "host" not in pg_props
    assert "host" not in set(pg_tool.public_input_schema.get("required", []))


def test_kubernetes_contract_requires_cluster_and_namespace_filters() -> None:
    tool_map = get_registered_tool_map()
    eks_tool = tool_map["list_eks_pods"]
    required = set(eks_tool.public_input_schema.get("required", []))
    assert {"cluster_name", "namespace"} <= required


def test_metrics_contracts_hide_credentials_from_model_visible_schema() -> None:
    tool_map = get_registered_tool_map()
    grafana = tool_map["query_grafana_metrics"]
    datadog = tool_map["query_datadog_metrics"]

    grafana_props = set(grafana.public_input_schema.get("properties", {}).keys())
    datadog_props = set(datadog.public_input_schema.get("properties", {}).keys())

    assert {"grafana_endpoint", "grafana_api_key", "grafana_backend"}.isdisjoint(grafana_props)
    assert {"api_key", "app_key", "site"}.isdisjoint(datadog_props)


def test_selection_scenarios_reference_valid_registered_tools() -> None:
    from tests.tools.selection.test_tool_selection import SELECTION_SCENARIOS

    tool_map = get_registered_tool_map()
    for scenario in SELECTION_SCENARIOS:
        for candidate in scenario.candidate_tool_names:
            assert candidate in tool_map, (
                f"Selection scenario {scenario.scenario_id!r} candidate tool {candidate!r} is not registered"
            )
        for expected in scenario.expected_tool_names:
            assert expected in tool_map, (
                f"Selection scenario {scenario.scenario_id!r} expected tool {expected!r} is not registered"
            )
        assert scenario.expected_tool_names.issubset(set(scenario.candidate_tool_names)), (
            f"Selection scenario {scenario.scenario_id!r} expected tools must be in candidates"
        )

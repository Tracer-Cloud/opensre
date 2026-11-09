from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import ValidationError

from config.llm_auth.credentials import status as credential_status
from config.llm_auth.provider_catalog import provider_spec
from config.llm_credentials import resolve_env_credential
from config.llm_settings import (
    get_configured_llm_provider,
    get_llm_provider_api_key_env,
    resolve_llm_settings,
)
from core.llm.factory import LLMRole, get_llm
from core.llm.shared.llm_retry import LLMCreditExhaustedError
from core.llm.types import SchemaDescribedTool
from tests.core.agent._ci_gates import skip_or_fail
from tools.registry import get_registered_tool_map

pytestmark = pytest.mark.integration

_TOOL_SELECTION_SYSTEM_PROMPT = (
    "You are selecting the first diagnostic tool for a live contract test. "
    "You must call exactly one of the provided tools. Do not answer in prose "
    "and do not merely name the tool."
)


@dataclass(frozen=True, slots=True)
class SelectionScenario:
    """Live tool-selection scenario definition."""

    scenario_id: str
    prompt: str
    candidate_tool_names: tuple[str, ...]
    expected_tool_names: frozenset[str]


SELECTION_SCENARIOS: Sequence[SelectionScenario] = (
    SelectionScenario(
        scenario_id="eks_pod_crashloop",
        prompt=(
            "Alert: Pod payment-service-5d8f7b in namespace default on cluster prod-eks "
            "is stuck in CrashLoopBackOff. Check the pods in this cluster and namespace."
        ),
        candidate_tool_names=(
            "list_eks_pods",
            "describe_eks_cluster",
            "get_postgresql_slow_queries",
            "search_sentry_issues",
            "query_datadog_metrics",
        ),
        expected_tool_names=frozenset({"list_eks_pods", "describe_eks_cluster"}),
    ),
    SelectionScenario(
        scenario_id="postgres_slow_queries",
        prompt=(
            "Alert: High query latency on PostgreSQL database orders_db. "
            "Inspect running slow queries exceeding the execution threshold."
        ),
        candidate_tool_names=(
            "get_postgresql_slow_queries",
            "get_postgresql_lock_status",
            "list_eks_pods",
            "search_sentry_issues",
            "describe_rds_instance",
        ),
        expected_tool_names=frozenset(
            {"get_postgresql_slow_queries", "get_postgresql_lock_status"}
        ),
    ),
    SelectionScenario(
        scenario_id="sentry_error_spike",
        prompt=(
            "Alert: Elevated 500 error rate in checkout service. "
            "Search for unresolved issues and recent exception stack traces in Sentry."
        ),
        candidate_tool_names=(
            "search_sentry_issues",
            "get_sentry_issue_details",
            "list_eks_pods",
            "get_postgresql_slow_queries",
            "query_grafana_metrics",
        ),
        expected_tool_names=frozenset({"search_sentry_issues", "get_sentry_issue_details"}),
    ),
    SelectionScenario(
        scenario_id="datadog_host_metrics",
        prompt=(
            "Alert: CPU utilization spike on worker node ip-10-0-1-42. "
            "Query Datadog metrics for system.cpu.user and system.mem.used on this host."
        ),
        candidate_tool_names=(
            "query_datadog_metrics",
            "query_datadog_monitors",
            "list_eks_pods",
            "search_sentry_issues",
            "get_postgresql_slow_queries",
        ),
        expected_tool_names=frozenset({"query_datadog_metrics", "query_datadog_monitors"}),
    ),
)


def _assert_scenario_tools_are_registered(
    scenario: SelectionScenario, tool_map: Mapping[str, object]
) -> None:
    for name in scenario.candidate_tool_names:
        assert name in tool_map, (
            f"Scenario {scenario.scenario_id!r} references unregistered candidate tool {name!r}"
        )
    for name in scenario.expected_tool_names:
        assert name in tool_map, (
            f"Scenario {scenario.scenario_id!r} references unregistered expected tool {name!r}"
        )


@pytest.mark.parametrize(
    "scenario",
    SELECTION_SCENARIOS,
    ids=lambda s: s.scenario_id,
)
def test_tool_selection_scenario_references_registered_tools(
    scenario: SelectionScenario,
) -> None:
    """Keep scenario metadata valid even when live LLM tests are unavailable."""
    _assert_scenario_tools_are_registered(scenario, get_registered_tool_map())


def _require_live_llm_credentials() -> None:
    settings: Any = None
    try:
        settings = resolve_llm_settings()
    except ValidationError as exc:
        provider = get_configured_llm_provider()
        env_var = get_llm_provider_api_key_env(provider)
        msg = exc.errors()[0].get("msg", str(exc)) if exc.errors() else str(exc)
        hint = f" configured provider={provider!r}"
        if env_var is not None:
            hint += f", required key={env_var}"
        skip_or_fail(f"Live tool selection requires LLM configuration:{hint}. {msg}")

    if settings is None:
        skip_or_fail("Live tool selection requires resolvable LLM settings.")

    auth = credential_status(settings.provider)
    if not auth.configured or auth.stale:
        skip_or_fail(
            "Live tool selection requires configured, current credentials for "
            f"provider {settings.provider!r}."
        )

    spec = provider_spec(settings.provider)
    if (
        spec is not None
        and spec.credential_kind == "api_key"
        and spec.api_key_env
        and not resolve_env_credential(spec.api_key_env)
    ):
        skip_or_fail(
            f"Live tool selection requires a resolvable API key for provider {settings.provider!r}."
        )


@pytest.mark.parametrize(
    "scenario",
    SELECTION_SCENARIOS,
    ids=lambda s: s.scenario_id,
)
@pytest.mark.live_llm
def test_live_tool_selection_matches_target_tool(scenario: SelectionScenario) -> None:
    """Assert live LLM selects an appropriate tool for the given incident context.

    Quarantine policy: If a scenario exhibits non-deterministic failure due to model drift,
    it must be quarantined with an explicit tracking issue and named owner:
    ``@pytest.mark.skip(reason="Quarantined: issue #<id> owner: @<github-handle>")``.
    Never loosen or weaken assertions to paper over intermittent failures.
    """
    _require_live_llm_credentials()

    tool_map = get_registered_tool_map()
    _assert_scenario_tools_are_registered(scenario, tool_map)

    candidate_tools: list[SchemaDescribedTool] = [
        tool_map[name] for name in scenario.candidate_tool_names
    ]
    assert len(candidate_tools) >= 2, (
        f"Scenario {scenario.scenario_id} must have multiple candidate tools"
    )

    llm = get_llm(LLMRole.AGENT)
    tool_schemas = llm.tool_schemas(candidate_tools)

    messages = [
        {
            "role": "user",
            "content": f"{scenario.prompt}\nSelect the single most relevant tool to begin diagnosing this issue.",
        }
    ]

    response = None
    try:
        response = llm.invoke(
            messages=messages,
            system=_TOOL_SELECTION_SYSTEM_PROMPT,
            tools=tool_schemas,
        )
    except LLMCreditExhaustedError as exc:
        skip_or_fail(f"Live tool selection provider credit/quota is exhausted. {exc}")
    assert response is not None

    assert response.has_tool_calls, (
        f"Model did not issue a tool call for scenario {scenario.scenario_id!r}. Content: {response.content!r}"
    )

    selected_names = [call.name for call in response.tool_calls]
    assert any(name in scenario.expected_tool_names for name in selected_names), (
        f"Scenario {scenario.scenario_id!r}: expected one of {sorted(scenario.expected_tool_names)}, "
        f"got {selected_names}"
    )

"""Characterization: top chat log tools expose ACI-shaped metadata."""

from __future__ import annotations

import pytest

from tools.registry import get_registered_tools


@pytest.fixture(scope="module")
def chat_tools() -> dict[str, object]:
    return {t.name: t for t in get_registered_tools("chat")}


@pytest.mark.parametrize(
    ("name", "must_mention", "required_contains"),
    [
        ("get_cloudwatch_logs", "log_group", ["log_group"]),
        ("kubernetes_get_pod_logs", "pod_name", ["pod_name"]),
        ("query_grafana_logs", "service_name", ["service_name"]),
        ("query_datadog_logs", "query", ["query"]),
        (
            "get_eks_pod_logs",
            "cluster_name",
            ["cluster_name", "namespace", "pod_name"],
        ),
    ],
)
def test_log_tool_aci_contract(
    chat_tools: dict[str, object],
    name: str,
    must_mention: str,
    required_contains: list[str],
) -> None:
    tool = chat_tools[name]
    description = str(getattr(tool, "description", "") or "")
    anti = list(getattr(tool, "anti_examples", None) or [])
    required = list((getattr(tool, "input_schema", None) or {}).get("required") or [])

    assert must_mention in description
    assert anti, f"{name} needs anti_examples"
    for key in required_contains:
        assert key in required

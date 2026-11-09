"""Regression tests for Grafana tool runtime-parameter injection."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from integrations.grafana import tools as grafana_tools
from integrations.grafana.tools import _GRAFANA_RUNTIME_PARAMS

GRAFANA_TOOL_FUNCTIONS: tuple[Callable[..., dict[str, Any]], ...] = (
    grafana_tools.query_grafana_alert_rules,
    grafana_tools.query_grafana_annotations,
    grafana_tools.query_grafana_logs,
    grafana_tools.query_grafana_metrics,
    grafana_tools.query_grafana_service_names,
    grafana_tools.query_grafana_traces,
)

GRAFANA_RUNTIME_PARAMS = set(_GRAFANA_RUNTIME_PARAMS)


def _tool_id(tool_function: Callable[..., dict[str, Any]]) -> str:
    return tool_function.__name__


@pytest.mark.parametrize("tool_function", GRAFANA_TOOL_FUNCTIONS, ids=_tool_id)
def test_grafana_connection_params_are_runtime_injected(
    tool_function: Callable[..., dict[str, Any]],
) -> None:
    """Grafana connection details come from config and must never be model input."""
    registered = tool_function.__opensre_registered_tool__  # type: ignore[attr-defined]

    assert set(registered.injected_params) >= GRAFANA_RUNTIME_PARAMS

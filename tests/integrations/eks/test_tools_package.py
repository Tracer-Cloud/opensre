"""Package-layout contract for ``integrations.eks.tools``.

The EKS tools live one-module-per-tool and are registered by ``@tool`` at
import time. Nothing imports those modules explicitly — the registry
discovers them by walking the package — so a tool can silently vanish from
the registry by being moved, renamed, or left out of a re-export. These
tests pin that seam.
"""

from __future__ import annotations

import importlib

import pytest

from core.tool import RegisteredTool
from integrations.eks import tools as eks_tools
from tools.registry import get_registered_tools

# Every EKS tool, mapped to the module that must own it. Adding a tool means
# adding a row here — that is the point: the registry set is asserted, not eyeballed.
EXPECTED_TOOL_MODULES: dict[str, str] = {
    "describe_eks_addon": "eks_describe_addon_tool",
    "describe_eks_cluster": "eks_describe_cluster_tool",
    "get_eks_deployment_status": "eks_deployment_status_tool",
    "get_eks_events": "eks_events_tool",
    "get_eks_node_health": "eks_node_health_tool",
    "get_eks_nodegroup_health": "eks_nodegroup_health_tool",
    "get_eks_pod_logs": "eks_pod_logs_tool",
    "list_eks_clusters": "eks_list_clusters_tool",
    "list_eks_deployments": "eks_list_deployments_tool",
    "list_eks_namespaces": "eks_list_namespaces_tool",
    "list_eks_pods": "eks_list_pods_tool",
}


PACKAGE = "integrations.eks.tools"


def _registered_eks_tools() -> dict[str, RegisteredTool]:
    """Registered tools contributed by ``integrations.eks.tools``.

    Selected by origin module rather than ``source``: the registry snapshot is
    process-wide, and the CloudOpsBench suite registers external bench tools
    that also declare ``source="eks"``.
    """
    return {
        t.name: t
        for t in get_registered_tools()
        if (t.origin_module or "").startswith(f"{PACKAGE}.")
    }


def test_registry_lists_every_eks_tool() -> None:
    """Discovery must still find all 11 tools after the one-module-per-tool split."""
    assert set(_registered_eks_tools()) == set(EXPECTED_TOOL_MODULES)


@pytest.mark.parametrize(("tool_name", "module_name"), sorted(EXPECTED_TOOL_MODULES.items()))
def test_tool_is_registered_from_its_own_module(tool_name: str, module_name: str) -> None:
    """Each tool registers from its own submodule, not from the package ``__init__``.

    Guards the split itself: re-collapsing tools into ``__init__.py`` would
    still register them, so only the origin module catches the regression.
    """
    registered = _registered_eks_tools()[tool_name]
    assert registered.origin_module == f"{PACKAGE}.{module_name}"
    assert registered.source == "eks"


def test_package_reexports_every_tool_and_nothing_else() -> None:
    """``__init__`` re-exports exactly the tool functions, each the module's own object."""
    assert sorted(eks_tools.__all__) == sorted(EXPECTED_TOOL_MODULES)
    for tool_name, module_name in EXPECTED_TOOL_MODULES.items():
        module = importlib.import_module(f"{PACKAGE}.{module_name}")
        assert getattr(eks_tools, tool_name) is getattr(module, tool_name)

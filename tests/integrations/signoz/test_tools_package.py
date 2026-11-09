"""Package-layout contract for ``integrations.signoz.tools``.

The SigNoz tools live one-package-per-tool and are registered by ``@tool`` at
import time. Nothing imports those modules explicitly — the registry discovers
them by walking the package — so a tool can silently vanish from the registry
by being moved, renamed, or dropped from a ``TOOL_MODULES`` manifest.
"""

from __future__ import annotations

from tools.registry import get_registered_tools

PACKAGE = "integrations.signoz.tools"

# Every SigNoz tool, mapped to the module that must own it. Adding a tool means
# adding a row here — the registry set is asserted, not eyeballed.
EXPECTED_TOOL_MODULES: dict[str, str] = {
    "query_signoz_logs": "query_signoz_logs_tool.tool",
    "query_signoz_metrics": "query_signoz_metrics_tool.tool",
    "query_signoz_traces": "query_signoz_traces_tool.tool",
}


def test_every_signoz_tool_registers_from_its_own_package() -> None:
    """All three tools stay registered, each from its own tool package."""
    registered = {
        t.name: t.origin_module
        for t in get_registered_tools()
        if (t.origin_module or "").startswith(f"{PACKAGE}.")
    }
    assert registered == {
        name: f"{PACKAGE}.{module}" for name, module in EXPECTED_TOOL_MODULES.items()
    }

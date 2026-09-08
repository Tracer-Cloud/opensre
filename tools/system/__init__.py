"""System-level tool packages: no external vendor/integration in their domain purpose.

Each entry is a sibling package under ``tools/system/``. Listed in
``TOOL_MODULES`` so the registry (:mod:`tools.registry`) walks one level
deeper than the default top-level scan and picks up any agent-callable
tools they define, alongside plain support packages that expose none.

See ``docs/tool-placement-policy.md`` (T-20) for the system vs.
cross_vendor vs. vendor-integration placement rules.
"""

from __future__ import annotations

TOOL_MODULES = (
    "agent_memory.tool",
    "fleet_monitoring",
    "python_execution_tool",
    "sre_guidance_tool",
    "work_items.tool",
    "workspace_git_scan.tool",
)

__all__ = ["TOOL_MODULES"]

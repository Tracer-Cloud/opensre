"""Cross-vendor tool packages: a single tool's logic spans 2+ vendor integrations.

Each entry is a sibling package under ``tools/cross_vendor/``. Listed in
``TOOL_MODULES`` so the registry (:mod:`tools.registry`) walks one level
deeper than the default top-level scan and picks up the agent-callable
tools they define.

See ``docs/tool-placement-policy.md`` (T-20) for the system vs.
cross_vendor vs. vendor-integration placement rules.
"""

from __future__ import annotations

TOOL_MODULES = ("fix_sentry_issue", "resolve_merge_conflicts.tool")

__all__ = ["TOOL_MODULES"]

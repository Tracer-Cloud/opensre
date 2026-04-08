"""Tool routing node with explainable selection and safe fallback paths.

This module provides deterministic tool selection with human-readable inclusion
reasons and bounded fallback for low-confidence routing scenarios.
"""

from __future__ import annotations

from app.nodes.route_tools.route_tools import (
    DEFAULT_FALLBACK_TOOLSETS,
    MAX_FALLBACK_TOOLS,
    MIN_CONFIDENCE_THRESHOLD,
    RoutingResult,
    ToolSelectionResult,
    filter_available_tools,
    route_tools_by_tags,
    route_tools_by_toolset,
    select_fallback_tools,
)

__all__ = [
    "DEFAULT_FALLBACK_TOOLSETS",
    "MAX_FALLBACK_TOOLS",
    "MIN_CONFIDENCE_THRESHOLD",
    "RoutingResult",
    "ToolSelectionResult",
    "filter_available_tools",
    "route_tools_by_tags",
    "route_tools_by_toolset",
    "select_fallback_tools",
]

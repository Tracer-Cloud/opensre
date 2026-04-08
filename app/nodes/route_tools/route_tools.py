"""Tool routing with explainable selection and safe fallback paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.tools.registered_tool import RegisteredTool

# Default fallback toolsets for low-confidence routing scenarios
# Tools selected deterministically when confidence is below threshold
DEFAULT_FALLBACK_TOOLSETS: frozenset[str] = frozenset({"core", "discovery", "logs"})

# Minimum confidence threshold for normal routing
# Below this, use deterministic fallback selection
MIN_CONFIDENCE_THRESHOLD: float = 0.5

# Maximum number of tools in fallback selection
MAX_FALLBACK_TOOLS: int = 3


@dataclass
class ToolSelectionResult:
    """Result of tool selection with human-readable inclusion reasons."""

    tool: RegisteredTool
    inclusion_reason: str = ""  # Human-readable explanation for why this tool was selected
    confidence: float = 1.0  # Confidence score for this selection (0.0-1.0)
    is_fallback: bool = False  # Whether this was selected as part of fallback


@dataclass
class RoutingResult:
    """Complete routing result with selected tools and metadata."""

    selected_tools: list[ToolSelectionResult] = field(default_factory=list)
    fallback_used: bool = False  # Whether fallback selection was used
    routing_reason: str = ""  # Overall explanation for routing decisions
    confidence: float = 1.0  # Overall confidence score

    def to_tool_names(self) -> list[str]:
        """Return list of selected tool names."""
        return [result.tool.name for result in self.selected_tools]

    def to_detailed_dict(self) -> dict[str, Any]:
        """Return detailed routing result as dictionary for logging/debugging."""
        return {
            "tools": [
                {
                    "name": result.tool.name,
                    "reason": result.inclusion_reason,
                    "confidence": result.confidence,
                    "is_fallback": result.is_fallback,
                    "toolset": result.tool.toolset,
                    "cost_hint": result.tool.cost_hint,
                    "tags": result.tool.tags,
                }
                for result in self.selected_tools
            ],
            "fallback_used": self.fallback_used,
            "routing_reason": self.routing_reason,
            "overall_confidence": self.confidence,
        }


def select_fallback_tools(
    available_tools: list[RegisteredTool],
    max_tools: int = MAX_FALLBACK_TOOLS,
) -> list[ToolSelectionResult]:
    """Deterministically select fallback tools when confidence is low.

    Fallback selection prioritizes:
    1. Tools from core toolsets (discovery, logs, core)
    2. Low cost_hint tools first
    3. Alphabetical by toolset, then by name for determinism

    Args:
        available_tools: All available tools to choose from
        max_tools: Maximum number of tools to select

    Returns:
        List of ToolSelectionResult with fallback selections and reasons
    """
    # Filter to fallback-eligible tools
    eligible = [tool for tool in available_tools if tool.toolset in DEFAULT_FALLBACK_TOOLSETS]

    if not eligible:
        # If no eligible tools in default toolsets, use any available
        eligible = available_tools[: max_tools * 2]

    # Sort by: cost_hint priority (low < medium < high), then toolset, then name
    cost_priority = {"low": 0, "medium": 1, "high": 2}
    sorted_tools = sorted(
        eligible,
        key=lambda t: (
            cost_priority.get(t.cost_hint, 0),
            t.toolset,
            t.name,
        ),
    )

    selected = sorted_tools[:max_tools]

    results = []
    for tool in selected:
        reason = (
            f"Fallback selection: {tool.toolset} tool with {tool.cost_hint} cost "
            f"(deterministic fallback for low-confidence routing)"
        )
        results.append(
            ToolSelectionResult(
                tool=tool,
                inclusion_reason=reason,
                confidence=0.5,  # Fixed confidence for fallback
                is_fallback=True,
            )
        )

    return results


def route_tools_by_tags(
    available_tools: list[RegisteredTool],
    required_tags: list[str],
    min_confidence: float = MIN_CONFIDENCE_THRESHOLD,
) -> RoutingResult:
    """Route tools by matching tags with explainable selection.

    Args:
        available_tools: All available tools
        required_tags: Tags that must be present for tool selection
        min_confidence: Minimum confidence threshold

    Returns:
        RoutingResult with selected tools and reasons
    """
    if not required_tags:
        # No tags specified - use fallback
        fallback_results = select_fallback_tools(available_tools)
        return RoutingResult(
            selected_tools=fallback_results,
            fallback_used=True,
            routing_reason="No routing tags specified - using deterministic fallback selection",
            confidence=0.5,
        )

    matching_tools = []
    for tool in available_tools:
        # Calculate match score (number of matching tags)
        matching_tags = set(tool.tags) & set(required_tags)
        if matching_tags:
            # Confidence based on proportion of required tags matched
            confidence = len(matching_tags) / len(required_tags)
            matching_tools.append((tool, matching_tags, confidence))

    if not matching_tools:
        # No matching tools - use fallback
        fallback_results = select_fallback_tools(available_tools)
        return RoutingResult(
            selected_tools=fallback_results,
            fallback_used=True,
            routing_reason=f"No tools match required tags {required_tags} - using fallback",
            confidence=0.3,
        )

    # Sort by confidence (highest first), then by cost_hint
    cost_priority = {"low": 0, "medium": 1, "high": 2}
    matching_tools.sort(key=lambda x: (-x[2], cost_priority.get(x[0].cost_hint, 0), x[0].name))

    # Select tools with confidence above threshold
    selected: list[ToolSelectionResult] = []
    overall_confidence = 0.0
    for tool, matching_tags, confidence in matching_tools:
        if confidence >= min_confidence or not selected:
            reason = f"Tag match: {', '.join(sorted(matching_tags))} (confidence: {confidence:.0%})"
            selected.append(
                ToolSelectionResult(
                    tool=tool,
                    inclusion_reason=reason,
                    confidence=confidence,
                    is_fallback=False,
                )
            )
            overall_confidence = max(overall_confidence, confidence)

    # If overall confidence is too low, use fallback instead
    if overall_confidence < min_confidence:
        fallback_results = select_fallback_tools(available_tools)
        return RoutingResult(
            selected_tools=fallback_results,
            fallback_used=True,
            routing_reason=f"Low confidence ({overall_confidence:.0%}) below threshold - using fallback",
            confidence=overall_confidence,
        )

    return RoutingResult(
        selected_tools=selected,
        fallback_used=False,
        routing_reason=f"Tag-based routing matched {len(selected)} tools for tags: {required_tags}",
        confidence=overall_confidence,
    )


def route_tools_by_toolset(
    available_tools: list[RegisteredTool],
    target_toolsets: list[str],
) -> RoutingResult:
    """Route tools by toolset with explainable selection.

    Args:
        available_tools: All available tools
        target_toolsets: Toolsets to prioritize

    Returns:
        RoutingResult with selected tools and reasons
    """
    if not target_toolsets:
        # No toolsets specified - use fallback
        fallback_results = select_fallback_tools(available_tools)
        return RoutingResult(
            selected_tools=fallback_results,
            fallback_used=True,
            routing_reason="No target toolsets specified - using deterministic fallback selection",
            confidence=0.5,
        )

    # Normalize target toolsets
    target_set = {ts.lower() for ts in target_toolsets}

    matching_tools = []
    for tool in available_tools:
        if tool.toolset.lower() in target_set:
            confidence = 1.0  # Full confidence for exact toolset match
            matching_tools.append((tool, confidence))

    if not matching_tools:
        # No matching toolsets - use fallback
        fallback_results = select_fallback_tools(available_tools)
        return RoutingResult(
            selected_tools=fallback_results,
            fallback_used=True,
            routing_reason=f"No tools in target toolsets {target_toolsets} - using fallback",
            confidence=0.3,
        )

    # Sort by cost_hint, then name for determinism
    cost_priority = {"low": 0, "medium": 1, "high": 2}
    matching_tools.sort(key=lambda x: (cost_priority.get(x[0].cost_hint, 0), x[0].name))

    selected = []
    overall_confidence = 1.0
    for tool, confidence in matching_tools:
        reason = f"Toolset match: {tool.toolset} (exact match, {tool.cost_hint} cost)"
        selected.append(
            ToolSelectionResult(
                tool=tool,
                inclusion_reason=reason,
                confidence=confidence,
                is_fallback=False,
            )
        )

    return RoutingResult(
        selected_tools=selected,
        fallback_used=False,
        routing_reason=f"Toolset-based routing matched {len(selected)} tools for toolsets: {target_toolsets}",
        confidence=overall_confidence,
    )


def filter_available_tools(
    tools: list[RegisteredTool],
    available_sources: dict[str, dict],
) -> list[RegisteredTool]:
    """Filter tools based on available data sources.

    Args:
        tools: All registered tools
        available_sources: Available data sources

    Returns:
        List of tools that can be executed with available sources
    """
    return [tool for tool in tools if tool.is_available(available_sources)]

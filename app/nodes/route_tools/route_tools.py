"""Tool routing logic for investigation planning.

Scores and selects the most relevant tools for the current investigation context,
reducing the toolset sent to planning to improve speed and reliability.
"""

from __future__ import annotations

from typing import Any

from app.tools.investigation_registry import get_available_actions
from app.tools.registered_tool import RegisteredTool


def _extract_keywords(problem_md: str, alert_name: str) -> list[str]:
    """Extract keywords from problem statement and alert name.

    Args:
        problem_md: Problem statement markdown
        alert_name: Alert name

    Returns:
        List of keywords extracted from the inputs
    """
    keywords: set[str] = set()

    # Add meaningful words from problem statement
    if problem_md:
        # Remove common markdown and punctuation
        cleaned = problem_md.lower()
        for char in "#*[](){}|`\"':;,.!?-=+_/\\":
            cleaned = cleaned.replace(char, " ")

        # Filter for meaningful words (length > 2)
        stop_words = {
            "the",
            "and",
            "for",
            "are",
            "but",
            "not",
            "you",
            "all",
            "can",
            "had",
            "her",
            "was",
            "one",
            "our",
            "out",
            "day",
            "get",
            "has",
            "him",
            "his",
            "how",
            "its",
            "may",
            "new",
            "now",
            "old",
            "see",
            "two",
            "who",
            "boy",
            "did",
            "she",
            "use",
            "way",
            "many",
            "oil",
            "sit",
            "set",
            "run",
            "eat",
            "far",
            "sea",
            "eye",
            "ago",
            "off",
            "too",
            "any",
            "say",
            "man",
            "try",
            "ask",
            "end",
            "why",
            "let",
            "put",
            "own",
        }

        for word in cleaned.split():
            if len(word) > 2 and word not in stop_words:
                keywords.add(word)

    # Add alert name parts
    if alert_name:
        # Split on common separators
        alert_parts = alert_name.lower().replace("-", " ").replace("_", " ").replace(".", " ")
        for part in alert_parts.split():
            if len(part) > 2:
                keywords.add(part)

    return list(keywords)


def score_tool_for_context(
    tool: RegisteredTool,
    available_sources: dict[str, dict],
    keywords: list[str],
    executed_actions: set[str],
) -> int:
    """Score a tool based on its relevance to the investigation context.

    Scoring criteria:
    - Source match: +5 points if tool's source is in available_sources
    - Keyword match in use_cases: +2 points per matching keyword
    - Keyword match in description: +1 point per matching keyword
    - Already executed: -10 points (deprioritize)

    Args:
        tool: The tool to score
        available_sources: Dictionary of detected available sources
        keywords: List of keywords from problem/alert
        executed_actions: Set of already executed action names

    Returns:
        Integer score (higher = more relevant)
    """
    score = 0

    # Deprioritize already executed actions
    if tool.name in executed_actions:
        score -= 10

    # Source match is a strong signal (+5)
    if tool.source in available_sources:
        score += 5

    # Keyword matching
    if keywords:
        keywords_lower = [kw.lower() for kw in keywords]

        # Match in use_cases (+2 per match)
        use_cases_text = " ".join(tool.use_cases).lower()
        for keyword in keywords_lower:
            if keyword in use_cases_text:
                score += 2

        # Match in description (+1 per match)
        description_lower = tool.description.lower()
        for keyword in keywords_lower:
            if keyword in description_lower:
                score += 1

    return score


def route_tools(
    available_sources: dict[str, dict],
    _resolved_integrations: dict[str, Any] | None,
    problem_md: str,
    alert_name: str,
    executed_hypotheses: list[dict[str, Any]],
    top_k: int | None = None,
) -> list[RegisteredTool]:
    """Route investigation to the most relevant tools.

    Scores all available tools based on:
    - Match with available data sources
    - Keyword relevance from problem statement and alert
    - Prior execution history (deprioritizes already executed)

    Args:
        available_sources: Dictionary of detected available sources
        resolved_integrations: Pre-resolved integration credentials
        problem_md: Problem statement markdown
        alert_name: Alert name for keyword extraction
        executed_hypotheses: History of executed hypotheses
        top_k: If provided, limit to top K tools (None = all with score > 0)

    Returns:
        List of RegisteredTool objects sorted by relevance score (highest first)

    Note:
        This function is deterministic - the same inputs will always
        produce the same output ordering.
    """
    # Get all available investigation tools
    all_tools = get_available_actions()

    # Extract executed actions from history
    executed_actions: set[str] = set()
    for hyp in executed_hypotheses:
        actions = hyp.get("actions", [])
        if isinstance(actions, list):
            executed_actions.update(actions)

    # Extract keywords from problem and alert
    keywords = _extract_keywords(problem_md, alert_name)

    # Score and filter tools
    scored_tools: list[tuple[RegisteredTool, int]] = []

    for tool in all_tools:
        # Check if tool is available for the detected sources
        if not tool.is_available(available_sources):
            continue

        # Score the tool
        score = score_tool_for_context(
            tool=tool,
            available_sources=available_sources,
            keywords=keywords,
            executed_actions=executed_actions,
        )

        # Include all available tools (even with negative scores from execution penalty)
        # This ensures deprioritization works and prevents silent fallback to legacy
        scored_tools.append((tool, score))

    # Sort by score (descending), then by name (ascending for determinism)
    scored_tools.sort(key=lambda x: (-x[1], x[0].name))

    # Apply top_k limit if specified
    if top_k is not None:
        scored_tools = scored_tools[:top_k]

    # Return just the tools (without scores)
    return [tool for tool, _ in scored_tools]


def route_tools_with_scores(
    available_sources: dict[str, dict],
    _resolved_integrations: dict[str, Any] | None,
    problem_md: str,
    alert_name: str,
    executed_hypotheses: list[dict[str, Any]],
    top_k: int | None = None,
) -> list[tuple[RegisteredTool, int]]:
    """Route investigation to the most relevant tools with scores.

    Same as route_tools() but returns tuples of (tool, score) for debugging.

    Args:
        available_sources: Dictionary of detected available sources
        resolved_integrations: Pre-resolved integration credentials
        problem_md: Problem statement markdown
        alert_name: Alert name for keyword extraction
        executed_hypotheses: History of executed hypotheses
        top_k: If provided, limit to top K tools

    Returns:
        List of (RegisteredTool, score) tuples sorted by score
    """
    all_tools = get_available_actions()

    executed_actions: set[str] = set()
    for hyp in executed_hypotheses:
        actions = hyp.get("actions", [])
        if isinstance(actions, list):
            executed_actions.update(actions)

    keywords = _extract_keywords(problem_md, alert_name)

    scored_tools: list[tuple[RegisteredTool, int]] = []

    for tool in all_tools:
        if not tool.is_available(available_sources):
            continue

        score = score_tool_for_context(
            tool=tool,
            available_sources=available_sources,
            keywords=keywords,
            executed_actions=executed_actions,
        )

        # Include all available tools (even with negative scores)
        scored_tools.append((tool, score))

    scored_tools.sort(key=lambda x: (-x[1], x[0].name))

    if top_k is not None:
        scored_tools = scored_tools[:top_k]

    return scored_tools

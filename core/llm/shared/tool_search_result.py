"""Read what a ``tool_search`` result reports, as a provider client receives it."""

from __future__ import annotations

import json
from typing import Any

from config.constants.tool_discovery import TOOL_SEARCH_MATCHES_KEY


def tool_search_matches(content: Any) -> list[dict[str, Any]]:
    """The match records a tool search result reports; ``[]`` when it cannot be read.

    A client sends a tool's output as a JSON-encoded string, so the payload may
    arrive encoded twice. Output cut to the tool-output budget, or replaced by
    the context-budget stub, no longer parses and reports nothing.
    """
    if not isinstance(content, str):
        return []
    try:
        payload: Any = json.loads(content)
        if isinstance(payload, str):
            payload = json.loads(payload)
    except ValueError:
        return []
    matches = payload.get(TOOL_SEARCH_MATCHES_KEY) if isinstance(payload, dict) else None
    if not isinstance(matches, list):
        return []
    return [
        match for match in matches if isinstance(match, dict) and isinstance(match.get("name"), str)
    ]


__all__ = ["tool_search_matches"]

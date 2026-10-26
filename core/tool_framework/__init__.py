"""Tool authoring helpers: the ``@tool`` decorator, planning tags, skill guidance.

The tier's public API for consumers above it. Schema and payload utilities live behind
the sibling ``core.tool_framework.utils`` package entry; the tool contract itself (what a
tool is, how it runs, where it is registered) is ``core.tool``.
"""

from core.tool_framework.skill_guidance import (
    format_tool_skill_guidance,
    load_tool_skill_guidance,
)
from core.tool_framework.tags import FALLBACK_PLANNING_TAG, SUMMARIZE_OBSERVATION_TAG
from core.tool_framework.tool_decorator import tool

__all__ = [
    "FALLBACK_PLANNING_TAG",
    "SUMMARIZE_OBSERVATION_TAG",
    "format_tool_skill_guidance",
    "load_tool_skill_guidance",
    "tool",
]

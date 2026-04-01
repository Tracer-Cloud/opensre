"""SRE knowledge investigation tools."""

from app.tools.tool_actions.base import BaseTool
from app.tools.tool_actions.knowledge_sre_book.sre_knowledge_actions import (
    SREGuidanceTool,
    get_sre_guidance,
)

TOOLS: list[BaseTool] = [SREGuidanceTool()]

__all__ = ["TOOLS", "SREGuidanceTool", "get_sre_guidance"]

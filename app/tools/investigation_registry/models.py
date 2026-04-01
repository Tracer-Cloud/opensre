"""Investigation action type — BaseTool is the canonical action representation."""

from app.tools.base import BaseTool

# InvestigationAction is now an alias for BaseTool.
# All callers that import InvestigationAction continue to work unchanged.
InvestigationAction = BaseTool

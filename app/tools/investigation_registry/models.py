"""Investigation action type — RegisteredTool is the canonical representation."""

from app.tools.registered_tool import RegisteredTool

# InvestigationAction is now an alias for RegisteredTool.
# All callers that import InvestigationAction continue to work unchanged.
InvestigationAction = RegisteredTool

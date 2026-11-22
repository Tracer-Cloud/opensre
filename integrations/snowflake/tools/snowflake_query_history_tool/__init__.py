"""Public Snowflake query-history tool API."""

from integrations.snowflake.tools.snowflake_query_history_tool.tool import query_snowflake_history

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "query_snowflake_history"]

"""Public CloudWatch Logs tool API."""

from integrations.cloudwatch.tools.cloudwatch_logs_tool.tool import get_cloudwatch_logs

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "get_cloudwatch_logs"]

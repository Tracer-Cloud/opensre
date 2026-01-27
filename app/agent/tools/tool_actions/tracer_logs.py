"""
Tracer logs tool actions - LangChain tool implementation.

Runtime log data from pipeline execution.
No printing, no LLM calls. Just fetch data and return typed results.
All functions are decorated with @tool for LangChain/LangGraph compatibility.
"""

from __future__ import annotations

try:
    from langchain.tools import tool
except ImportError:
    # Fallback if langchain not available - create a no-op decorator
    def tool(func=None, **kwargs):  # noqa: ARG001
        if func is None:
            return lambda f: f
        return func


from app.agent.tools.clients.tracer_client import get_tracer_web_client


def get_error_logs(trace_id: str, size: int = 500, error_only: bool = True) -> dict:
    """
    Get logs from OpenSearch, optionally filtered for errors.

    Useful for:
    - Proving error pattern hypothesis
    - Finding root cause error messages
    - Understanding failure timeline

    Args:
        trace_id: The trace/run identifier
        size: Maximum number of logs to retrieve (default 500)
        error_only: If True, return only error/failure logs; if False, return all logs

    Returns:
        Dictionary with logs list and metadata
    """
    if not trace_id:
        return {"error": "trace_id is required"}

    client = get_tracer_web_client()
    logs_data = client.get_logs(run_id=trace_id, size=size)

    # Handle API response structure
    if not isinstance(logs_data, dict):
        logs_data = {"data": [], "success": False}
    if "data" not in logs_data:
        logs_data = {"data": logs_data if isinstance(logs_data, list) else [], "success": True}

    log_list = logs_data.get("data", [])

    if error_only:
        filtered_logs = [
            {
                "message": log.get("message", "")[:500],
                "log_level": log.get("log_level"),
                "timestamp": log.get("timestamp"),
            }
            for log in log_list
            if "error" in str(log.get("log_level", "")).lower()
            or "fail" in str(log.get("message", "")).lower()
        ][:50]  # Limit to 50 most recent errors
    else:
        filtered_logs = [
            {
                "message": log.get("message", "")[:500],
                "log_level": log.get("log_level"),
                "timestamp": log.get("timestamp"),
            }
            for log in log_list
        ][:200]  # Limit to 200 most recent logs

    return {
        "logs": filtered_logs,
        "total_logs": len(log_list),
        "filtered_count": len(filtered_logs),
        "error_only": error_only,
        "source": "opensearch/logs API",
    }


# Create LangChain tool from the function
get_error_logs_tool = tool(get_error_logs)

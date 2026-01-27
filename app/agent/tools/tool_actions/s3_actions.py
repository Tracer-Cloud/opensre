"""
S3 tool actions - LangChain tool implementation.

No printing, no LLM calls. Just fetch data and return typed results.
All functions are decorated with @tool for LangChain/LangGraph compatibility.
"""

try:
    from langchain.tools import tool
except ImportError:
    # Fallback if langchain not available - create a no-op decorator
    def tool(func=None, **kwargs):  # noqa: ARG001
        if func is None:
            return lambda f: f
        return func


from app.agent.tools.clients.s3_client import S3CheckResult, get_s3_client


def check_s3_marker(bucket: str, prefix: str) -> S3CheckResult:
    """
    Check if _SUCCESS marker exists in S3 storage.

    Use this tool to verify if a data pipeline run completed successfully by checking
    for the presence of a _SUCCESS marker file in the specified S3 location.

    Args:
        bucket: S3 bucket name
        prefix: S3 key prefix (path) where the marker should be located

    Returns:
        S3CheckResult with marker existence status and file count
    """
    client = get_s3_client()
    return client.check_marker(bucket, prefix)


# Create LangChain tool from the function
check_s3_marker_tool = tool(check_s3_marker)

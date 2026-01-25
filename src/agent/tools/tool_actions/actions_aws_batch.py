"""
AWS Batch tool actions - LangChain tool implementation.

AWS Batch-specific operations via CloudWatch and AWS APIs.
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


# Note: AWS Batch job actions are now organized by data type:
# - get_batch_jobs -> actions_tracer_jobs.py (job execution state)
# - get_failed_jobs -> actions_tracer_jobs.py (job execution results)
# - get_batch_statistics -> actions_tracer_metrics.py (aggregated metrics)
#
# This file is kept for potential future AWS Batch-specific CloudWatch/API actions
# that don't go through Tracer's API.

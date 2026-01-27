"""
CloudWatch tool actions - LangChain tool implementation.

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


from app.agent.tools.clients.cloudwatch_client import get_metric_statistics


def get_cloudwatch_batch_metrics(job_queue: str, metric_type: str = "cpu") -> dict:
    """
    Get CloudWatch metrics for AWS Batch jobs.

    Useful for:
    - Proving resource constraint hypothesis
    - Understanding batch job performance
    - Identifying AWS infrastructure issues

    Args:
        job_queue: The AWS Batch job queue name
        metric_type: Either 'cpu' or 'memory'

    Returns:
        Dictionary with CloudWatch metrics
    """
    if not job_queue:
        return {"error": "job_queue is required"}

    if metric_type not in ["cpu", "memory"]:
        return {"error": "metric_type must be 'cpu' or 'memory'"}

    try:
        if metric_type == "cpu":
            metrics = get_metric_statistics(
                namespace="AWS/Batch",
                metric_name="CPUUtilization",
                dimensions=[{"Name": "JobQueue", "Value": job_queue}],
                statistics=["Average", "Maximum"],
            )
        else:
            metrics = get_metric_statistics(
                namespace="AWS/Batch",
                metric_name="MemoryUtilization",
                dimensions=[{"Name": "JobQueue", "Value": job_queue}],
                statistics=["Average", "Maximum"],
            )

        return {
            "metrics": metrics,
            "metric_type": metric_type,
            "job_queue": job_queue,
            "source": "AWS CloudWatch API",
        }
    except Exception as e:
        return {"error": f"CloudWatch not available: {str(e)}"}


# Create LangChain tool from the function
get_cloudwatch_batch_metrics_tool = tool(get_cloudwatch_batch_metrics)

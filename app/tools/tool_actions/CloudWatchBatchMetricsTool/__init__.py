"""CloudWatch metrics for AWS Batch jobs."""

from __future__ import annotations

from app.tools.clients.cloudwatch_client import get_metric_statistics
from app.tools.tool_actions.base import BaseTool
from app.tools.tool_decorator import tool


class CloudWatchBatchMetricsTool(BaseTool):
    """Get CloudWatch metrics for AWS Batch jobs."""

    name = "get_cloudwatch_batch_metrics"
    source = "cloudwatch"
    description = "Get CloudWatch metrics for AWS Batch jobs."
    use_cases = [
        "Proving resource constraint hypothesis",
        "Understanding batch job performance",
        "Identifying AWS infrastructure issues",
    ]
    requires = ["job_queue"]
    input_schema = {
        "type": "object",
        "properties": {
            "job_queue": {"type": "string", "description": "The AWS Batch job queue name"},
            "metric_type": {"type": "string", "enum": ["cpu", "memory"], "default": "cpu"},
        },
        "required": ["job_queue"],
    }

    def run(self, job_queue: str, metric_type: str = "cpu", **_kwargs) -> dict:
        if not job_queue:
            return {"error": "job_queue is required"}
        if metric_type not in ["cpu", "memory"]:
            return {"error": "metric_type must be 'cpu' or 'memory'"}

        try:
            metric_name = "CPUUtilization" if metric_type == "cpu" else "MemoryUtilization"
            metrics = get_metric_statistics(
                namespace="AWS/Batch",
                metric_name=metric_name,
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


get_cloudwatch_batch_metrics = CloudWatchBatchMetricsTool()
get_cloudwatch_batch_metrics_tool = tool(get_cloudwatch_batch_metrics)

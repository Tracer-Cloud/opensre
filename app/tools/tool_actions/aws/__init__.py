"""AWS investigation tools (CloudWatch, S3, Lambda, SDK)."""

from app.tools.tool_actions.aws.aws_sdk_actions import AWSOperationTool, execute_aws_operation
from app.tools.tool_actions.aws.cloudwatch_actions import (
    CloudWatchBatchMetricsTool,
    CloudWatchLogsTool,
    get_cloudwatch_batch_metrics,
    get_cloudwatch_batch_metrics_tool,
    get_cloudwatch_logs,
)
from app.tools.tool_actions.aws.lambda_actions import (
    LambdaConfigTool,
    LambdaErrorsTool,
    LambdaInspectTool,
    LambdaInvocationLogsTool,
    get_lambda_configuration,
    get_lambda_errors,
    get_lambda_invocation_logs,
    inspect_lambda_function,
)
from app.tools.tool_actions.aws.s3_actions import (
    S3GetObjectTool,
    S3InspectTool,
    S3ListTool,
    S3MarkerTool,
    check_s3_marker,
    check_s3_object_exists,
    compare_s3_versions,
    get_s3_object,
    inspect_s3_object,
    list_s3_objects,
    list_s3_versions,
)
from app.tools.tool_actions.base import BaseTool

# Canonical tool list registered with the investigation registry.
TOOLS: list[BaseTool] = [
    CloudWatchLogsTool(),
    S3MarkerTool(),
    S3InspectTool(),
    S3ListTool(),
    S3GetObjectTool(),
    LambdaInvocationLogsTool(),
    LambdaErrorsTool(),
    LambdaInspectTool(),
    LambdaConfigTool(),
    AWSOperationTool(),
]

__all__ = [
    "TOOLS",
    "AWSOperationTool",
    "CloudWatchBatchMetricsTool",
    "CloudWatchLogsTool",
    "LambdaConfigTool",
    "LambdaErrorsTool",
    "LambdaInspectTool",
    "LambdaInvocationLogsTool",
    "S3GetObjectTool",
    "S3InspectTool",
    "S3ListTool",
    "S3MarkerTool",
    # Backward-compatible aliases
    "check_s3_marker",
    "check_s3_object_exists",
    "compare_s3_versions",
    "execute_aws_operation",
    "get_cloudwatch_batch_metrics",
    "get_cloudwatch_batch_metrics_tool",
    "get_cloudwatch_logs",
    "get_lambda_configuration",
    "get_lambda_errors",
    "get_lambda_invocation_logs",
    "get_s3_object",
    "inspect_lambda_function",
    "inspect_s3_object",
    "list_s3_objects",
    "list_s3_versions",
]

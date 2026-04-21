"""AWS SDK generic operation tool."""

from __future__ import annotations

from typing import Any

from app.services.aws_sdk_client import execute_aws_sdk_call
from app.tools.tool_decorator import tool


def _aws_operation_never_auto_available(_sources: dict[str, dict]) -> bool:
    # Disabled for automatic planning until service/operation can be safely derived from context.
    return False


@tool(
    name="execute_aws_operation",
    source="aws_sdk",
    description="Execute any read-only AWS SDK operation for investigation.",
    use_cases=[
        "Checking ECS task status and health (ecs.describe_tasks)",
        "Inspecting RDS database configuration (rds.describe_db_instances)",
        "Reviewing VPC networking setup (ec2.describe_vpcs)",
        "Examining IAM role permissions (iam.get_role)",
        "Investigating EC2 instance state (ec2.describe_instances)",
        "Querying CloudFormation stack details (cloudformation.describe_stacks)",
        "Checking EFS mount targets (efs.describe_mount_targets)",
        "Reviewing Systems Manager parameters (ssm.get_parameter)",
        "Inspecting Step Functions executions (stepfunctions.describe_execution)",
        "Checking Secrets Manager secrets metadata (secretsmanager.describe_secret)",
    ],
    requires=["service", "operation"],
    input_schema={
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "description": "AWS service name (e.g., 'ecs', 'rds', 'ec2', 'lambda')",
            },
            "operation": {
                "type": "string",
                "description": "Operation name (e.g., 'describe_tasks', 'get_role')",
            },
            "parameters": {"type": "object", "description": "Operation parameters as dict"},
        },
        "required": ["service", "operation"],
    },
    is_available=_aws_operation_never_auto_available,
)
def execute_aws_operation(
    service: str,
    operation: str,
    parameters: dict[str, Any] | None = None,
) -> dict:
    """Execute any read-only AWS SDK operation for investigation."""
    if not service or not operation:
        return {
            "found": False,
            "error": "service and operation are required",
            "service": service,
            "operation": operation,
        }

    result = execute_aws_sdk_call(
        service_name=service,
        operation_name=operation,
        parameters=parameters,
    )

    if not result.get("success"):
        return {
            "found": False,
            "service": service,
            "operation": operation,
            "error": result.get("error", "Unknown error"),
            "metadata": result.get("metadata", {}),
        }

    return {
        "found": True,
        "service": service,
        "operation": operation,
        "result": result.get("data", {}),
        "metadata": result.get("metadata", {}),
    }

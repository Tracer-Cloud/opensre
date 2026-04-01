"""
AWS SDK tool actions - Generic read-only AWS operations.

Provides flexible AWS SDK access for investigation scenarios where
dedicated actions don't exist.
"""

from typing import Any

from app.tools.clients.aws_sdk_client import execute_aws_sdk_call


def execute_aws_operation(
    service: str,
    operation: str,
    parameters: dict[str, Any] | None = None,
) -> dict:
    """
    Execute any read-only AWS SDK operation for investigation.

    Use this when you need to call AWS APIs that don't have dedicated actions.
    Essential for investigating infrastructure-level issues across AWS services.

    IMPORTANT: Only read-only operations are allowed (describe_*, get_*, list_*, etc.).
    Write operations will be rejected for safety.

    Useful for:
    - Checking ECS task status and health (ecs.describe_tasks)
    - Inspecting RDS database configuration (rds.describe_db_instances)
    - Reviewing VPC networking setup (ec2.describe_vpcs)
    - Examining IAM role permissions (iam.get_role)
    - Investigating EC2 instance state (ec2.describe_instances)
    - Querying CloudFormation stack details (cloudformation.describe_stacks)
    - Checking EFS mount targets (efs.describe_mount_targets)
    - Reviewing Systems Manager parameters (ssm.get_parameter)
    - Inspecting Step Functions executions (stepfunctions.describe_execution)
    - Checking Secrets Manager secrets metadata (secretsmanager.describe_secret)

    Examples:
        # Check ECS task status
        execute_aws_operation(
            service="ecs",
            operation="describe_tasks",
            parameters={"cluster": "prod-cluster", "tasks": ["task-arn"]}
        )

        # Get RDS instance details
        execute_aws_operation(
            service="rds",
            operation="describe_db_instances",
            parameters={"DBInstanceIdentifier": "prod-postgres"}
        )

        # List Lambda function versions
        execute_aws_operation(
            service="lambda",
            operation="list_versions_by_function",
            parameters={"FunctionName": "my-function"}
        )

    Args:
        service: AWS service name (e.g., 'ecs', 'rds', 'ec2', 'lambda')
        operation: Operation name (e.g., 'describe_tasks', 'get_role')
        parameters: Operation parameters as dict (e.g., {"cluster": "prod"})

    Returns:
        Dictionary with operation results:
        {
            "found": bool,
            "service": str,
            "operation": str,
            "result": dict,
            "error": str (if failed)
        }
    """
    if not service or not operation:
        return {
            "found": False,
            "error": "service and operation are required",
            "service": service,
            "operation": operation,
        }

    # Execute SDK call through client
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

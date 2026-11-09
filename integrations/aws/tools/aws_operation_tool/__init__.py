"""AWS SDK generic operation tool."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.tool_framework import tool
from integrations.aws.aws_sdk_client import execute_aws_sdk_call


def _aws_operation_never_auto_available(_sources: dict[str, dict]) -> bool:
    # Disabled for automatic planning until service/operation can be safely derived from context.
    return False


def _summarize_aws_result(result: Any) -> str:
    """Describe an AWS operation payload by shape only.

    Never echoes payload values: an operation may return role policies or secret
    metadata, and the summary is rendered into the incident report.
    """
    if isinstance(result, dict):
        if not result:
            return "empty result"
        return "1 top-level key" if len(result) == 1 else f"{len(result)} top-level keys"
    if isinstance(result, list):
        if not result:
            return "empty result"
        return "1 record" if len(result) == 1 else f"{len(result)} records"
    if result is None:
        return "empty result"
    return f"{type(result).__name__} result"


def _map_aws_operation(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    if not output.get("found"):
        return

    service = str(output.get("service") or "")
    operation = str(output.get("operation") or "")
    qualified = ".".join(part for part in (service, operation) if part) or "operation"
    evidence["aws_operation"] = output.get("result")

    record_evidence_entry(
        evidence,
        source="execute_aws_operation",
        label=f"AWS {qualified}",
        summary=_summarize_aws_result(output.get("result")),
    )


@tool(
    name="execute_aws_operation",
    source="aws_sdk",
    evidence_mapper=_map_aws_operation,
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

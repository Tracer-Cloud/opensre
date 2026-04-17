"""
Generic AWS SDK client for executing read-only operations.

Security-first design with operation allowlists and response sanitization.
"""

import re
from typing import Any

import boto3
from botocore.exceptions import ClientError, NoCredentialsError, ParamValidationError

# Read-only operation patterns (allowlist)
ALLOWED_OPERATION_PATTERNS = [
    r"^describe_.*",
    r"^get_.*",
    r"^list_.*",
    r"^head_.*",
    r"^query$",
    r"^scan$",
    r"^select_.*",
    r"^batch_get_.*",
    r"^lookup_.*",
]

# Destructive operation patterns (blocklist - extra safety layer)
BLOCKED_OPERATION_PATTERNS = [
    r".*delete.*",
    r".*remove.*",
    r".*update.*",
    r".*put.*",
    r".*create.*",
    r".*modify.*",
    r".*terminate.*",
    r".*stop.*",
    r".*start.*",
    r".*reboot.*",
    r".*attach.*",
    r".*detach.*",
    r".*associate.*",
    r".*disassociate.*",
]

# Response size limits
MAX_RESPONSE_SIZE_BYTES = 100_000
MAX_LIST_ITEMS = 100
MAX_PAGINATION_CALLS = 5


def _is_operation_allowed(operation_name: str) -> tuple[bool, str]:
    """
    Validate that operation is read-only and safe to execute.

    Args:
        operation_name: AWS SDK operation name (e.g., 'describe_instances')

    Returns:
        Tuple of (is_allowed, reason)
    """
    operation_lower = operation_name.lower()

    # Check blocklist first (fail fast on dangerous operations)
    for pattern in BLOCKED_OPERATION_PATTERNS:
        if re.match(pattern, operation_lower):
            return False, f"Operation '{operation_name}' matches blocked pattern '{pattern}'"

    # Check allowlist
    for pattern in ALLOWED_OPERATION_PATTERNS:
        if re.match(pattern, operation_lower):
            return True, "Operation allowed"

    return False, f"Operation '{operation_name}' does not match any allowed patterns"


def _sanitize_response(data: Any, depth: int = 0, max_depth: int = 10) -> Any:
    """
    Sanitize AWS response data for safe consumption.

    - Converts datetime objects to ISO strings
    - Truncates large collections
    - Handles binary data
    - Prevents excessive nesting

    Args:
        data: Response data to sanitize
        depth: Current recursion depth
        max_depth: Maximum recursion depth

    Returns:
        Sanitized data
    """
    if depth > max_depth:
        return "... (max depth reached)"

    # Handle None
    if data is None:
        return None

    # Handle datetime objects
    if hasattr(data, "isoformat"):
        return data.isoformat()

    # Handle bytes
    if isinstance(data, bytes):
        return f"<binary data: {len(data)} bytes>"

    # Handle dictionaries
    if isinstance(data, dict):
        sanitized = {}
        for key, value in data.items():
            # Skip ResponseMetadata (noise)
            if key == "ResponseMetadata":
                continue
            sanitized[key] = _sanitize_response(value, depth + 1, max_depth)
        return sanitized

    # Handle lists/tuples
    if isinstance(data, list | tuple):
        if len(data) > MAX_LIST_ITEMS:
            truncated = [
                _sanitize_response(item, depth + 1, max_depth) for item in data[:MAX_LIST_ITEMS]
            ]
            truncated.append(f"... ({len(data) - MAX_LIST_ITEMS} more items truncated)")
            return truncated
        return [_sanitize_response(item, depth + 1, max_depth) for item in data]

    # Handle primitive types
    return data


def execute_aws_sdk_call(
    service_name: str,
    operation_name: str,
    parameters: dict[str, Any] | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """
    Execute a read-only AWS SDK operation with safety validations.

    Args:
        service_name: AWS service name (e.g., 'ec2', 'rds', 'ecs')
        operation_name: Operation to call (e.g., 'describe_instances')
        parameters: Operation parameters as dict
        region: Optional AWS region override

    Returns:
        Dictionary with standardized response:
        {
            "success": bool,
            "service": str,
            "operation": str,
            "data": dict | None,
            "error": str | None,
            "metadata": dict
        }
    """
    if not service_name or not operation_name:
        return {
            "success": False,
            "error": "service_name and operation_name are required",
            "service": service_name,
            "operation": operation_name,
            "data": None,
            "metadata": {},
        }

    # Validate operation is allowed
    is_allowed, reason = _is_operation_allowed(operation_name)
    if not is_allowed:
        return {
            "success": False,
            "error": f"Operation not allowed: {reason}",
            "service": service_name,
            "operation": operation_name,
            "data": None,
            "metadata": {"validation_failed": True},
        }

    try:
        # Create boto3 client
        client_kwargs: dict[str, str] = {}
        if region:
            client_kwargs["region_name"] = region

        client = boto3.client(service_name, **client_kwargs)  # type: ignore[call-overload]

        # Verify operation exists
        if not hasattr(client, operation_name):
            return {
                "success": False,
                "error": f"Operation '{operation_name}' not found in service '{service_name}'",
                "service": service_name,
                "operation": operation_name,
                "data": None,
                "metadata": {"available_operations": dir(client)[:20]},
            }

        # Execute operation
        operation = getattr(client, operation_name)
        if parameters:
            response = operation(**parameters)
        else:
            response = operation()

        # Sanitize response
        sanitized_data = _sanitize_response(response)

        return {
            "success": True,
            "service": service_name,
            "operation": operation_name,
            "data": sanitized_data,
            "error": None,
            "metadata": {
                "region": client.meta.region_name,
                "parameters_provided": bool(parameters),
            },
        }

    except NoCredentialsError as e:
        return {
            "success": False,
            "error": f"AWS credentials not configured: {str(e)}",
            "service": service_name,
            "operation": operation_name,
            "data": None,
            "metadata": {"error_type": "credentials"},
        }

    except ParamValidationError as e:
        return {
            "success": False,
            "error": f"Invalid parameters: {str(e)}",
            "service": service_name,
            "operation": operation_name,
            "data": None,
            "metadata": {"error_type": "validation", "parameters": parameters},
        }

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))

        return {
            "success": False,
            "error": f"AWS API error ({error_code}): {error_message}",
            "service": service_name,
            "operation": operation_name,
            "data": None,
            "metadata": {
                "error_type": "client_error",
                "error_code": error_code,
                "status_code": e.response.get("ResponseMetadata", {}).get("HTTPStatusCode"),
            },
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Unexpected error: {str(e)}",
            "service": service_name,
            "operation": operation_name,
            "data": None,
            "metadata": {"error_type": "unexpected"},
        }

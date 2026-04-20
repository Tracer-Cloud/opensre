"""Lambda function management."""

import io
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from tests.shared.infrastructure_sdk.deployer import (
    DEFAULT_REGION,
    get_boto3_client,
    get_standard_tags,
)


def bundle_code(source_dir: Path, requirements_file: Path | None = None) -> bytes:
    """Create deployment zip with dependencies.

    Args:
        source_dir: Directory containing Lambda code.
        requirements_file: Optional path to requirements.txt.

    Returns:
        Zip file contents as bytes.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        package_dir = tmp_path / "package"
        package_dir.mkdir()

        # Install dependencies if requirements file provided
        if requirements_file and requirements_file.exists():
            subprocess.run(
                [
                    "python3",
                    "-m",
                    "pip",
                    "install",
                    "-q",
                    "-r",
                    str(requirements_file),
                    "-t",
                    str(package_dir),
                    "--platform",
                    "manylinux2014_x86_64",
                    "--only-binary=:all:",
                ],
                check=True,
                capture_output=True,
            )

        # Copy source files
        for item in source_dir.iterdir():
            if item.is_file() and item.suffix == ".py":
                shutil.copy2(item, package_dir)
            elif item.is_dir() and not item.name.startswith("."):
                shutil.copytree(item, package_dir / item.name)

        # Create zip
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(package_dir):
                for file in files:
                    file_path = Path(root) / file
                    arcname = file_path.relative_to(package_dir)
                    zf.write(file_path, arcname)

        return zip_buffer.getvalue()


def bundle_single_file(handler_file: Path, requirements_file: Path | None = None) -> bytes:
    """Create deployment zip from a single handler file.

    Args:
        handler_file: Path to the handler Python file.
        requirements_file: Optional path to requirements.txt.

    Returns:
        Zip file contents as bytes.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        package_dir = tmp_path / "package"
        package_dir.mkdir()

        # Install dependencies if requirements file provided
        if requirements_file and requirements_file.exists():
            subprocess.run(
                [
                    "python3",
                    "-m",
                    "pip",
                    "install",
                    "-q",
                    "-r",
                    str(requirements_file),
                    "-t",
                    str(package_dir),
                    "--platform",
                    "manylinux2014_x86_64",
                    "--only-binary=:all:",
                ],
                check=True,
                capture_output=True,
            )

        # Copy handler file
        shutil.copy2(handler_file, package_dir)

        # Create zip
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(package_dir):
                for file in files:
                    file_path = Path(root) / file
                    arcname = file_path.relative_to(package_dir)
                    zf.write(file_path, arcname)

        return zip_buffer.getvalue()


def create_function(
    name: str,
    role_arn: str,
    handler: str,
    code_zip: bytes,
    runtime: str = "python3.11",
    timeout: int = 30,
    memory: int = 128,
    environment: dict[str, str] | None = None,
    stack_name: str | None = None,
    region: str = DEFAULT_REGION,
    layers: list[str] | None = None,
) -> dict[str, Any]:
    """Create Lambda function.

    Args:
        name: Function name.
        role_arn: ARN of the execution role.
        handler: Handler specification (e.g., "handler.lambda_handler").
        code_zip: Zip file bytes.
        runtime: Python runtime version.
        timeout: Function timeout in seconds.
        memory: Memory allocation in MB.
        environment: Environment variables.
        stack_name: Stack name for tagging.
        region: AWS region.
        layers: Optional list of layer ARNs.

    Returns:
        Dictionary with function info: arn, name, version.
    """
    lambda_client = get_boto3_client("lambda", region)

    # Prepare configuration
    config: dict[str, Any] = {
        "FunctionName": name,
        "Runtime": runtime,
        "Role": role_arn,
        "Handler": handler,
        "Code": {"ZipFile": code_zip},
        "Timeout": timeout,
        "MemorySize": memory,
    }

    if environment:
        config["Environment"] = {"Variables": environment}

    if stack_name:
        config["Tags"] = {t["Key"]: t["Value"] for t in get_standard_tags(stack_name)}

    if layers:
        config["Layers"] = layers

    # Retry logic for IAM propagation delays
    max_retries = 5
    retry_delay = 10  # seconds
    response: dict[str, Any] = {}

    for attempt in range(max_retries):
        try:
            response = lambda_client.create_function(**config)
            break
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ResourceConflictException":
                # Function exists, update it
                update_function_code(name, code_zip, region)
                return update_function_configuration(
                    name,
                    environment=environment,
                    timeout=timeout,
                    memory=memory,
                    region=region,
                )
            elif (
                error_code == "InvalidParameterValueException"
                and "cannot be assumed" in str(e)
                and attempt < max_retries - 1
            ):
                # IAM role not ready yet, retry
                time.sleep(retry_delay)
                continue
            raise

    # Wait for function to be active
    _wait_for_function_active(name, lambda_client)

    return {
        "arn": response["FunctionArn"],
        "name": response["FunctionName"],
        "version": response.get("Version", "$LATEST"),
    }


def update_function_code(
    name: str, code_zip: bytes, region: str = DEFAULT_REGION
) -> dict[str, Any]:
    """Update existing function code.

    Args:
        name: Function name.
        code_zip: Zip file bytes.
        region: AWS region.

    Returns:
        Dictionary with function info: arn, name, version.
    """
    lambda_client = get_boto3_client("lambda", region)

    response = lambda_client.update_function_code(
        FunctionName=name,
        ZipFile=code_zip,
    )

    # Wait for update to complete
    _wait_for_function_active(name, lambda_client)

    return {
        "arn": response["FunctionArn"],
        "name": response["FunctionName"],
        "version": response.get("Version", "$LATEST"),
    }


def update_function_configuration(
    name: str,
    environment: dict[str, str] | None = None,
    timeout: int | None = None,
    memory: int | None = None,
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """Update function configuration.

    Args:
        name: Function name.
        environment: New environment variables (replaces existing).
        timeout: New timeout in seconds.
        memory: New memory in MB.
        region: AWS region.

    Returns:
        Updated function info.
    """
    lambda_client = get_boto3_client("lambda", region)

    config: dict[str, Any] = {"FunctionName": name}

    if environment is not None:
        try:
            current_config = lambda_client.get_function_configuration(FunctionName=name)
            current_env = current_config.get("Environment", {}).get("Variables", {})
            merged_env = {**current_env, **environment}
        except ClientError:
            merged_env = dict(environment)
        config["Environment"] = {"Variables": merged_env}
    if timeout is not None:
        config["Timeout"] = timeout
    if memory is not None:
        config["MemorySize"] = memory

    response = lambda_client.update_function_configuration(**config)

    _wait_for_function_active(name, lambda_client)

    return {
        "arn": response["FunctionArn"],
        "name": response["FunctionName"],
        "version": response.get("Version", "$LATEST"),
    }


def delete_function(name: str, region: str = DEFAULT_REGION) -> None:
    """Delete Lambda function.

    Args:
        name: Function name.
        region: AWS region.
    """
    lambda_client = get_boto3_client("lambda", region)

    try:
        lambda_client.delete_function(FunctionName=name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise


def add_permission(
    function_name: str,
    statement_id: str,
    principal: str,
    source_arn: str | None = None,
    region: str = DEFAULT_REGION,
) -> None:
    """Add invoke permission to Lambda.

    Args:
        function_name: Function name.
        statement_id: Unique statement ID.
        principal: Service principal (e.g., "apigateway.amazonaws.com").
        source_arn: Optional source ARN for condition.
        region: AWS region.
    """
    lambda_client = get_boto3_client("lambda", region)

    params: dict[str, Any] = {
        "FunctionName": function_name,
        "StatementId": statement_id,
        "Action": "lambda:InvokeFunction",
        "Principal": principal,
    }

    if source_arn:
        params["SourceArn"] = source_arn

    try:
        lambda_client.add_permission(**params)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            pass  # Permission already exists
        else:
            raise


def invoke_function(
    function_name: str,
    payload: dict[str, Any] | None = None,
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """Invoke a Lambda function synchronously.

    Args:
        function_name: Function name.
        payload: Request payload.
        region: AWS region.

    Returns:
        Response payload.
    """
    import json

    lambda_client = get_boto3_client("lambda", region)

    params: dict[str, Any] = {
        "FunctionName": function_name,
        "InvocationType": "RequestResponse",
    }

    if payload:
        params["Payload"] = json.dumps(payload)

    response = lambda_client.invoke(**params)

    response_payload = response["Payload"].read()
    if response_payload:
        result: dict[str, Any] = json.loads(response_payload)
        return result
    return {}


def get_function(name: str, region: str = DEFAULT_REGION) -> dict[str, Any] | None:
    """Get function details.

    Args:
        name: Function name.
        region: AWS region.

    Returns:
        Function configuration or None if not found.
    """
    lambda_client = get_boto3_client("lambda", region)

    try:
        response = lambda_client.get_function(FunctionName=name)
        config: dict[str, Any] = response["Configuration"]
        return config
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return None
        raise


def _wait_for_function_active(name: str, client: Any, max_attempts: int = 30) -> None:
    """Wait for function to be in Active state."""
    for _ in range(max_attempts):
        response = client.get_function(FunctionName=name)
        state = response["Configuration"].get("State", "Active")
        last_update_status = response["Configuration"].get("LastUpdateStatus", "Successful")

        if state == "Active" and last_update_status in ("Successful", None):
            return

        time.sleep(2)

    raise TimeoutError(f"Lambda function {name} did not become active")

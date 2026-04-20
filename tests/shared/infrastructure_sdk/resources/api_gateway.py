"""REST API Gateway setup with Lambda integration."""

import time
from typing import Any

from botocore.exceptions import ClientError

from tests.shared.infrastructure_sdk.deployer import (
    DEFAULT_REGION,
    get_boto3_client,
    get_standard_tags,
)
from tests.shared.infrastructure_sdk.resources.iam import get_account_id


def create_rest_api(
    name: str,
    description: str,
    stack_name: str,
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """Create REST API.

    Args:
        name: API name.
        description: API description.
        stack_name: Stack name for tagging.
        region: AWS region.

    Returns:
        Dictionary with API info: api_id, root_resource_id, arn.
    """
    api_client = get_boto3_client("apigateway", region)

    # Create API
    response = api_client.create_rest_api(
        name=name,
        description=description,
        endpointConfiguration={"types": ["REGIONAL"]},
        tags={t["Key"]: t["Value"] for t in get_standard_tags(stack_name)},
    )

    api_id = response["id"]

    # Get root resource
    resources = api_client.get_resources(restApiId=api_id)
    root_resource_id = next(r["id"] for r in resources["items"] if r["path"] == "/")

    account_id = get_account_id(region)

    return {
        "api_id": api_id,
        "root_resource_id": root_resource_id,
        "arn": f"arn:aws:apigateway:{region}::/restapis/{api_id}",
        "account_id": account_id,
    }


def create_resource(
    api_id: str,
    parent_resource_id: str,
    path_part: str,
    region: str = DEFAULT_REGION,
) -> str:
    """Create API resource (path segment).

    Args:
        api_id: REST API ID.
        parent_resource_id: Parent resource ID.
        path_part: Path segment (e.g., "users", "{id}").
        region: AWS region.

    Returns:
        Resource ID.
    """
    api_client = get_boto3_client("apigateway", region)

    response = api_client.create_resource(
        restApiId=api_id,
        parentId=parent_resource_id,
        pathPart=path_part,
    )

    return str(response["id"])


def create_proxy_resource(
    api_id: str,
    parent_resource_id: str,
    region: str = DEFAULT_REGION,
) -> str:
    """Create proxy resource ({proxy+}).

    Args:
        api_id: REST API ID.
        parent_resource_id: Parent resource ID.
        region: AWS region.

    Returns:
        Resource ID.
    """
    return create_resource(api_id, parent_resource_id, "{proxy+}", region)


def create_lambda_integration(
    api_id: str,
    resource_id: str,
    lambda_arn: str,
    http_method: str = "ANY",
    region: str = DEFAULT_REGION,
) -> None:
    """Create Lambda proxy integration.

    Args:
        api_id: REST API ID.
        resource_id: Resource ID to add method to.
        lambda_arn: Lambda function ARN.
        http_method: HTTP method (GET, POST, ANY, etc.).
        region: AWS region.
    """
    api_client = get_boto3_client("apigateway", region)
    account_id = get_account_id(region)

    # Create method
    try:
        api_client.put_method(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod=http_method,
            authorizationType="NONE",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConflictException":
            raise

    # Create integration
    lambda_uri = (
        f"arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/{lambda_arn}/invocations"
    )

    api_client.put_integration(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod=http_method,
        type="AWS_PROXY",
        integrationHttpMethod="POST",
        uri=lambda_uri,
    )

    # Add Lambda permission
    from tests.shared.infrastructure_sdk.resources.lambda_ import add_permission

    # Extract function name from ARN
    function_name = lambda_arn.split(":")[-1]
    source_arn = f"arn:aws:execute-api:{region}:{account_id}:{api_id}/*/{http_method}/*"

    add_permission(
        function_name=function_name,
        statement_id=f"apigateway-{api_id}-{http_method}",
        principal="apigateway.amazonaws.com",
        source_arn=source_arn,
        region=region,
    )


def deploy_api(
    api_id: str,
    stage_name: str = "prod",
    region: str = DEFAULT_REGION,
) -> str:
    """Deploy API and return invoke URL.

    Args:
        api_id: REST API ID.
        stage_name: Stage name for deployment.
        region: AWS region.

    Returns:
        Invoke URL.
    """
    api_client = get_boto3_client("apigateway", region)

    # Create deployment
    api_client.create_deployment(
        restApiId=api_id,
        stageName=stage_name,
    )

    # Wait for deployment
    time.sleep(2)

    return f"https://{api_id}.execute-api.{region}.amazonaws.com/{stage_name}/"


def delete_api(api_id: str, region: str = DEFAULT_REGION) -> None:
    """Delete REST API.

    Args:
        api_id: REST API ID.
        region: AWS region.
    """
    api_client = get_boto3_client("apigateway", region)

    try:
        api_client.delete_rest_api(restApiId=api_id)
    except ClientError as e:
        if e.response["Error"]["Code"] != "NotFoundException":
            raise


def get_api(api_id: str, region: str = DEFAULT_REGION) -> dict[str, Any] | None:
    """Get API details.

    Args:
        api_id: REST API ID.
        region: AWS region.

    Returns:
        API details or None if not found.
    """
    api_client = get_boto3_client("apigateway", region)

    try:
        response = api_client.get_rest_api(restApiId=api_id)
        return {
            "api_id": response["id"],
            "name": response["name"],
            "description": response.get("description"),
        }
    except ClientError as e:
        if e.response["Error"]["Code"] == "NotFoundException":
            return None
        raise


def create_simple_api_with_lambda(
    api_name: str,
    lambda_arn: str,
    stack_name: str,
    stage_name: str = "prod",
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """Create a simple API Gateway with Lambda proxy integration.

    This is a convenience function that creates an API with a catch-all
    proxy resource that routes all requests to the Lambda function.

    Args:
        api_name: Name for the API.
        lambda_arn: Lambda function ARN.
        stack_name: Stack name for tagging.
        stage_name: Stage name for deployment.
        region: AWS region.

    Returns:
        Dictionary with: api_id, invoke_url, arn.
    """
    # Create API
    api_info = create_rest_api(
        name=api_name,
        description=f"API for {api_name}",
        stack_name=stack_name,
        region=region,
    )

    api_id = api_info["api_id"]
    root_resource_id = api_info["root_resource_id"]

    # Create proxy resource
    proxy_resource_id = create_proxy_resource(api_id, root_resource_id, region)

    # Add Lambda integration to root
    create_lambda_integration(api_id, root_resource_id, lambda_arn, "ANY", region)

    # Add Lambda integration to proxy
    create_lambda_integration(api_id, proxy_resource_id, lambda_arn, "ANY", region)

    # Deploy
    invoke_url = deploy_api(api_id, stage_name, region)

    return {
        "api_id": api_id,
        "invoke_url": invoke_url,
        "arn": api_info["arn"],
    }

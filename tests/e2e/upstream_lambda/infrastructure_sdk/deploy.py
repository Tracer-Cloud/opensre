#!/usr/bin/env python3
"""Deploy Lambda test case infrastructure using AWS SDK.

Creates:
- 2 S3 buckets (landing, processed)
- 3 Lambda functions (MockApi, Ingester, MockDag)
- 2 API Gateways (MockExternalApi, IngesterApi)
- 3 IAM roles with S3 permissions
- S3 event notification (landing -> MockDag)
"""

import io
import os
import shutil
import tempfile
import time
import uuid
import zipfile
from contextlib import suppress
from pathlib import Path

from botocore.exceptions import ClientError

from tests.shared.infrastructure_sdk.config import save_outputs
from tests.shared.infrastructure_sdk.deployer import get_boto3_client
from tests.shared.infrastructure_sdk.resources import api_gateway, iam, lambda_, s3
from tests.shared.infrastructure_sdk.resources.iam import get_account_id, put_role_policy
from tests.shared.infrastructure_sdk.resources.secrets import get_secret_value

project_root = Path(__file__).resolve().parents[3]

STACK_NAME = "tracer-lambda"
REGION = "us-east-1"


def bundle_pipeline_code(pipeline_dir: Path) -> bytes:
    """Bundle pipeline code with vendored dependencies at root level.

    The pipeline_code directory has dependencies vendored inside api_ingester/.
    This function restructures them to be at the root level for Lambda imports.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        package_dir = tmp_path / "package"
        package_dir.mkdir()

        # Copy the handler modules (api_ingester and mock_dag)
        for module in ["api_ingester", "mock_dag"]:
            module_src = pipeline_dir / module
            if module_src.exists():
                module_dst = package_dir / module
                module_dst.mkdir(exist_ok=True)
                # Copy only Python files and __init__.py
                for item in module_src.iterdir():
                    if item.name.endswith(".py"):
                        shutil.copy2(item, module_dst / item.name)
                    elif item.is_dir() and not item.name.endswith("-info"):
                        # Copy subdirectories (like adapters/)
                        shutil.copytree(item, module_dst / item.name, dirs_exist_ok=True)

        # Copy vendored dependencies from api_ingester to root level
        api_ingester_dir = pipeline_dir / "api_ingester"
        vendored_packages = [
            "requests", "urllib3", "certifi", "charset_normalizer", "idna"
        ]
        for pkg in vendored_packages:
            pkg_src = api_ingester_dir / pkg
            if pkg_src.exists():
                shutil.copytree(pkg_src, package_dir / pkg, dirs_exist_ok=True)

        # Create zip
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(package_dir):
                for file in files:
                    # Skip .pyc files and dist-info directories
                    if file.endswith(".pyc") or "-info" in root:
                        continue
                    file_path = Path(root) / file
                    arcname = file_path.relative_to(package_dir)
                    zf.write(file_path, arcname)

        return zip_buffer.getvalue()


def create_s3_buckets() -> dict:
    """Create S3 buckets for landing and processed data."""
    suffix = uuid.uuid4().hex[:8]

    landing_bucket_name = f"{STACK_NAME}-landing-{suffix}"
    processed_bucket_name = f"{STACK_NAME}-processed-{suffix}"

    print("Creating S3 buckets...")
    landing = s3.create_bucket(landing_bucket_name, STACK_NAME, REGION)
    processed = s3.create_bucket(processed_bucket_name, STACK_NAME, REGION)

    print(f"  - Landing: {landing['name']}")
    print(f"  - Processed: {processed['name']}")

    return {
        "landing_bucket": landing,
        "processed_bucket": processed,
    }


def create_iam_roles(landing_bucket: str, processed_bucket: str) -> dict:
    """Create IAM roles for Lambda functions."""
    print("Creating IAM roles...")

    # Create roles
    mock_api_role = iam.create_lambda_execution_role(
        f"{STACK_NAME}-mock-api-role",
        STACK_NAME,
        REGION,
    )

    ingester_role = iam.create_lambda_execution_role(
        f"{STACK_NAME}-ingester-role",
        STACK_NAME,
        REGION,
    )

    mock_dag_role = iam.create_lambda_execution_role(
        f"{STACK_NAME}-mock-dag-role",
        STACK_NAME,
        REGION,
    )

    # Add S3 write permission to ingester role
    put_role_policy(
        ingester_role["name"],
        "S3WritePolicy",
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:PutObject"],
                    "Resource": [f"arn:aws:s3:::{landing_bucket}/*"],
                }
            ],
        },
        REGION,
    )

    # Add S3 read/write permissions to mock_dag role
    put_role_policy(
        mock_dag_role["name"],
        "S3ReadWritePolicy",
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{landing_bucket}/*"],
                },
                {
                    "Effect": "Allow",
                    "Action": ["s3:PutObject"],
                    "Resource": [f"arn:aws:s3:::{processed_bucket}/*"],
                },
            ],
        },
        REGION,
    )

    print(f"  - Mock API role: {mock_api_role['name']}")
    print(f"  - Ingester role: {ingester_role['name']}")
    print(f"  - Mock DAG role: {mock_dag_role['name']}")

    return {
        "mock_api_role": mock_api_role,
        "ingester_role": ingester_role,
        "mock_dag_role": mock_dag_role,
    }


def create_lambda_functions(
    roles: dict,
    landing_bucket: str,
    processed_bucket: str,
    mock_api_url: str,
    grafana_secrets: dict,
) -> dict:
    """Create Lambda functions."""
    print("Creating Lambda functions...")

    # Paths
    shared_dir = project_root / "tests" / "shared" / "external_vendor_api"
    pipeline_dir = project_root / "tests" / "upstream_lambda" / "pipeline_code"

    # Bundle code
    print("  Bundling MockApi Lambda code...")
    mock_api_zip = lambda_.bundle_code(shared_dir)

    print("  Bundling pipeline code (dependencies already vendored)...")
    # Use custom bundling that puts vendored deps at root level
    pipeline_zip = bundle_pipeline_code(pipeline_dir)

    # Create MockApi Lambda
    print("  Creating MockApiLambda...")
    mock_api_lambda = lambda_.create_function(
        name=f"{STACK_NAME}-mock-api",
        role_arn=roles["mock_api_role"]["arn"],
        handler="handler.lambda_handler",
        code_zip=mock_api_zip,
        timeout=30,
        memory=128,
        stack_name=STACK_NAME,
        region=REGION,
    )

    # Create Ingester Lambda
    print("  Creating IngesterLambda...")
    ingester_lambda = lambda_.create_function(
        name=f"{STACK_NAME}-ingester",
        role_arn=roles["ingester_role"]["arn"],
        handler="api_ingester.handler.lambda_handler",
        code_zip=pipeline_zip,
        timeout=60,
        memory=128,
        environment={
            "LANDING_BUCKET": landing_bucket,
            "EXTERNAL_API_URL": mock_api_url,
            "OTEL_EXPORTER_OTLP_ENDPOINT": grafana_secrets.get("GCLOUD_OTLP_ENDPOINT", ""),
            "GCLOUD_OTLP_AUTH_HEADER": grafana_secrets.get("GCLOUD_OTLP_AUTH_HEADER", ""),
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_SERVICE_NAME": "lambda-api-ingester",
            "OTEL_RESOURCE_ATTRIBUTES": "pipeline.name=upstream_downstream_pipeline_lambda_ingester,pipeline.framework=lambda,test_case=test_case_upstream_lambda",
        },
        stack_name=STACK_NAME,
        region=REGION,
    )

    # Create MockDag Lambda
    print("  Creating MockDagLambda...")
    mock_dag_lambda = lambda_.create_function(
        name=f"{STACK_NAME}-mock-dag",
        role_arn=roles["mock_dag_role"]["arn"],
        handler="mock_dag.handler.lambda_handler",
        code_zip=pipeline_zip,
        timeout=300,
        memory=128,
        environment={
            "LANDING_BUCKET": landing_bucket,
            "PROCESSED_BUCKET": processed_bucket,
            "OTEL_EXPORTER_OTLP_ENDPOINT": grafana_secrets.get("GCLOUD_OTLP_ENDPOINT", ""),
            "GCLOUD_OTLP_AUTH_HEADER": grafana_secrets.get("GCLOUD_OTLP_AUTH_HEADER", ""),
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_SERVICE_NAME": "lambda-mock-dag",
            "OTEL_RESOURCE_ATTRIBUTES": "pipeline.name=upstream_downstream_pipeline_lambda,pipeline.framework=lambda,test_case=test_case_upstream_lambda",
        },
        stack_name=STACK_NAME,
        region=REGION,
    )

    print(f"  - MockApi: {mock_api_lambda['name']}")
    print(f"  - Ingester: {ingester_lambda['name']}")
    print(f"  - MockDag: {mock_dag_lambda['name']}")

    return {
        "mock_api_lambda": mock_api_lambda,
        "ingester_lambda": ingester_lambda,
        "mock_dag_lambda": mock_dag_lambda,
    }


def create_api_gateways(lambdas: dict) -> dict:
    """Create API Gateways for MockApi and Ingester."""
    print("Creating API Gateways...")

    # Create MockExternalApi
    mock_api = api_gateway.create_simple_api_with_lambda(
        api_name=f"{STACK_NAME}-mock-external-api",
        lambda_arn=lambdas["mock_api_lambda"]["arn"],
        stack_name=STACK_NAME,
        region=REGION,
    )

    # Create IngesterApi
    ingester_api = api_gateway.create_simple_api_with_lambda(
        api_name=f"{STACK_NAME}-ingester-api",
        lambda_arn=lambdas["ingester_lambda"]["arn"],
        stack_name=STACK_NAME,
        region=REGION,
    )

    print(f"  - MockExternalApi: {mock_api['invoke_url']}")
    print(f"  - IngesterApi: {ingester_api['invoke_url']}")

    return {
        "mock_api": mock_api,
        "ingester_api": ingester_api,
    }


def add_s3_trigger(landing_bucket: str, mock_dag_lambda_arn: str, mock_dag_lambda_name: str) -> None:
    """Add S3 event notification to trigger MockDag Lambda."""
    print("Adding S3 event notification...")

    s3_client = get_boto3_client("s3", REGION)
    lambda_client = get_boto3_client("lambda", REGION)
    account_id = get_account_id(REGION)

    # Add Lambda permission for S3
    with suppress(lambda_client.exceptions.ResourceConflictException):
        lambda_client.add_permission(
            FunctionName=mock_dag_lambda_name,
            StatementId=f"s3-trigger-{landing_bucket}",
            Action="lambda:InvokeFunction",
            Principal="s3.amazonaws.com",
            SourceArn=f"arn:aws:s3:::{landing_bucket}",
            SourceAccount=account_id,
        )

    # Configure S3 notification with retry to handle IAM propagation delays
    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        try:
            s3_client.put_bucket_notification_configuration(
                Bucket=landing_bucket,
                NotificationConfiguration={
                    "LambdaFunctionConfigurations": [
                        {
                            "LambdaFunctionArn": mock_dag_lambda_arn,
                            "Events": ["s3:ObjectCreated:*"],
                            "Filter": {
                                "Key": {
                                    "FilterRules": [
                                        {"Name": "prefix", "Value": "ingested/"},
                                    ]
                                }
                            },
                        }
                    ]
                },
            )
            break
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "InvalidArgument" and attempt < max_attempts:
                wait_seconds = attempt * 2
                print(
                    "  - Warning: S3 notification validation failed. "
                    f"Retrying in {wait_seconds}s (attempt {attempt}/{max_attempts})..."
                )
                time.sleep(wait_seconds)
                continue
            raise

    print(f"  - S3 trigger: {landing_bucket} -> {mock_dag_lambda_name}")


def update_ingester_env(ingester_lambda_name: str, mock_api_url: str) -> None:
    """Update ingester Lambda with the mock API URL."""
    print("Updating Ingester Lambda environment...")

    lambda_client = get_boto3_client("lambda", REGION)

    # Get current configuration
    response = lambda_client.get_function_configuration(FunctionName=ingester_lambda_name)
    env_vars = response.get("Environment", {}).get("Variables", {})
    if not isinstance(env_vars, dict):
        env_vars = {}

    # Update EXTERNAL_API_URL
    env_vars["EXTERNAL_API_URL"] = mock_api_url

    # Update function configuration
    lambda_.update_function_configuration(
        name=ingester_lambda_name,
        environment=env_vars,
        region=REGION,
    )
    print(f"  - Updated EXTERNAL_API_URL to: {mock_api_url}")


def deploy() -> dict:
    """Deploy all resources."""
    start_time = time.time()
    print("=" * 60)
    print(f"Deploying {STACK_NAME} infrastructure")
    print("=" * 60)
    print()

    # Get Grafana secrets
    print("Fetching Grafana secrets...")
    try:
        grafana_secrets = get_secret_value("tracer/grafana-cloud", REGION)
        print("  - Grafana secrets loaded")
    except Exception as e:
        print(f"  - Warning: Could not load Grafana secrets: {e}")
        grafana_secrets = {}

    # 1. Create S3 buckets
    buckets = create_s3_buckets()
    landing_bucket = buckets["landing_bucket"]["name"]
    processed_bucket = buckets["processed_bucket"]["name"]

    # 2. Create IAM roles
    roles = create_iam_roles(landing_bucket, processed_bucket)

    # 3. Create MockApi Lambda and API Gateway first (for URL)
    print("Creating MockApi Lambda...")
    shared_dir = project_root / "tests" / "shared" / "external_vendor_api"
    mock_api_zip = lambda_.bundle_code(shared_dir)

    mock_api_lambda = lambda_.create_function(
        name=f"{STACK_NAME}-mock-api",
        role_arn=roles["mock_api_role"]["arn"],
        handler="handler.lambda_handler",
        code_zip=mock_api_zip,
        timeout=30,
        memory=128,
        stack_name=STACK_NAME,
        region=REGION,
    )
    print(f"  - Created: {mock_api_lambda['name']}")

    # Create MockApi API Gateway
    print("Creating MockApi API Gateway...")
    mock_api_gw = api_gateway.create_simple_api_with_lambda(
        api_name=f"{STACK_NAME}-mock-external-api",
        lambda_arn=mock_api_lambda["arn"],
        stack_name=STACK_NAME,
        region=REGION,
    )
    mock_api_url = mock_api_gw["invoke_url"]
    print(f"  - URL: {mock_api_url}")

    # 4. Create pipeline Lambdas with correct URLs
    print("Creating pipeline Lambda functions...")
    pipeline_dir = project_root / "tests" / "upstream_lambda" / "pipeline_code"

    print("  Bundling pipeline code (dependencies already vendored)...")
    # Use custom bundling that puts vendored deps at root level
    pipeline_zip = bundle_pipeline_code(pipeline_dir)

    print("  Creating IngesterLambda...")
    ingester_lambda = lambda_.create_function(
        name=f"{STACK_NAME}-ingester",
        role_arn=roles["ingester_role"]["arn"],
        handler="api_ingester.handler.lambda_handler",
        code_zip=pipeline_zip,
        timeout=60,
        memory=128,
        environment={
            "LANDING_BUCKET": landing_bucket,
            "EXTERNAL_API_URL": mock_api_url,
            "OTEL_EXPORTER_OTLP_ENDPOINT": grafana_secrets.get("GCLOUD_OTLP_ENDPOINT", ""),
            "GCLOUD_OTLP_AUTH_HEADER": grafana_secrets.get("GCLOUD_OTLP_AUTH_HEADER", ""),
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_SERVICE_NAME": "lambda-api-ingester",
            "OTEL_RESOURCE_ATTRIBUTES": "pipeline.name=upstream_downstream_pipeline_lambda_ingester,pipeline.framework=lambda,test_case=test_case_upstream_lambda",
        },
        stack_name=STACK_NAME,
        region=REGION,
    )
    print(f"  - Created: {ingester_lambda['name']}")

    print("  Creating MockDagLambda...")
    mock_dag_lambda = lambda_.create_function(
        name=f"{STACK_NAME}-mock-dag",
        role_arn=roles["mock_dag_role"]["arn"],
        handler="mock_dag.handler.lambda_handler",
        code_zip=pipeline_zip,
        timeout=300,
        memory=128,
        environment={
            "LANDING_BUCKET": landing_bucket,
            "PROCESSED_BUCKET": processed_bucket,
            "OTEL_EXPORTER_OTLP_ENDPOINT": grafana_secrets.get("GCLOUD_OTLP_ENDPOINT", ""),
            "GCLOUD_OTLP_AUTH_HEADER": grafana_secrets.get("GCLOUD_OTLP_AUTH_HEADER", ""),
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_SERVICE_NAME": "lambda-mock-dag",
            "OTEL_RESOURCE_ATTRIBUTES": "pipeline.name=upstream_downstream_pipeline_lambda,pipeline.framework=lambda,test_case=test_case_upstream_lambda",
        },
        stack_name=STACK_NAME,
        region=REGION,
    )
    print(f"  - Created: {mock_dag_lambda['name']}")

    # 5. Create Ingester API Gateway
    print("Creating Ingester API Gateway...")
    ingester_api_gw = api_gateway.create_simple_api_with_lambda(
        api_name=f"{STACK_NAME}-ingester-api",
        lambda_arn=ingester_lambda["arn"],
        stack_name=STACK_NAME,
        region=REGION,
    )
    print(f"  - URL: {ingester_api_gw['invoke_url']}")

    # 6. Add S3 trigger for MockDag
    add_s3_trigger(landing_bucket, mock_dag_lambda["arn"], mock_dag_lambda["name"])

    # Prepare outputs (matching CDK output keys)
    outputs = {
        "MockApiUrl": mock_api_url,
        "IngesterApiUrl": ingester_api_gw["invoke_url"],
        "IngesterFunctionName": ingester_lambda["name"],
        "MockDagFunctionName": mock_dag_lambda["name"],
        "LandingBucketName": landing_bucket,
        "ProcessedBucketName": processed_bucket,
        # Additional internal references
        "MockApiLambdaArn": mock_api_lambda["arn"],
        "IngesterLambdaArn": ingester_lambda["arn"],
        "MockDagLambdaArn": mock_dag_lambda["arn"],
        "MockApiGatewayId": mock_api_gw["api_id"],
        "IngesterApiGatewayId": ingester_api_gw["api_id"],
    }

    # Save outputs
    save_outputs(STACK_NAME, outputs)

    elapsed = time.time() - start_time
    print()
    print("=" * 60)
    print(f"Deployment completed in {elapsed:.1f}s")
    print("=" * 60)
    print()
    print("Outputs:")
    print(f"  MockApiUrl: {outputs['MockApiUrl']}")
    print(f"  IngesterApiUrl: {outputs['IngesterApiUrl']}")
    print(f"  IngesterFunctionName: {outputs['IngesterFunctionName']}")
    print(f"  MockDagFunctionName: {outputs['MockDagFunctionName']}")
    print(f"  LandingBucketName: {outputs['LandingBucketName']}")
    print(f"  ProcessedBucketName: {outputs['ProcessedBucketName']}")

    return outputs


if __name__ == "__main__":
    deploy()

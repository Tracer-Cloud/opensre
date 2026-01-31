"""Infrastructure validation tests for upstream/downstream pipeline.

Tests that the AWS infrastructure and mock pipeline are working correctly.
"""

import json
import sys
import time
from datetime import datetime

import boto3
import requests


def get_stack_outputs(stack_name: str = "TracerUpstreamDownstreamTest") -> dict:
    """Get CloudFormation stack outputs."""
    cf_client = boto3.client("cloudformation")
    try:
        stack = cf_client.describe_stacks(StackName=stack_name)
        outputs = {o["OutputKey"]: o["OutputValue"] for o in stack["Stacks"][0]["Outputs"]}
        return outputs
    except Exception as e:
        print(f"ERROR: Could not get stack outputs: {e}")
        print(f"Make sure stack '{stack_name}' is deployed")
        sys.exit(1)


def test_happy_path(stack_outputs: dict) -> bool:
    """Test 1: Happy path with valid schema."""
    print("=" * 60)
    print("TEST 1: Happy Path")
    print("=" * 60)

    # Reset API to good schema
    try:
        response = requests.post(
            f"{stack_outputs['MockApiUrl']}/config",
            json={"inject_schema_change": False},
            timeout=10,
        )
        print("✓ Configured Mock API (good schema)")
    except Exception as e:
        print(f"✗ Failed to configure Mock API: {e}")
        return False

    # Verify API returns good data
    api_data = requests.get(f"{stack_outputs['MockApiUrl']}/data", timeout=10).json()
    has_customer_id = "customer_id" in api_data.get("data", [{}])[0]
    print(f"✓ Mock API returns data with customer_id: {has_customer_id}")
    if not has_customer_id:
        print("✗ TEST FAILED: Mock API should have customer_id")
        return False

    # Invoke ingester
    correlation_id = f"happy-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
    lambda_client = boto3.client("lambda")

    print(f"Invoking Ingester Lambda (correlation_id={correlation_id})...")
    response = lambda_client.invoke(
        FunctionName=stack_outputs["IngesterFunctionName"],
        Payload=json.dumps({"correlation_id": correlation_id}),
    )

    result = json.loads(response["Payload"].read())
    if result.get("statusCode") != 200:
        print(f"✗ Ingester failed: {result}")
        return False

    s3_key = result["s3_key"]
    print(f"✓ Data written to landing: {s3_key}")

    # Verify landing bucket has data
    s3_client = boto3.client("s3")
    try:
        obj = s3_client.get_object(Bucket=stack_outputs["LandingBucketName"], Key=s3_key)
        landing_data = json.loads(obj["Body"].read())
        has_customer_id = "customer_id" in landing_data.get("data", [{}])[0]
        print(f"✓ Landing data has customer_id: {has_customer_id}")

        if not has_customer_id:
            print("✗ TEST FAILED: Landing data should have customer_id")
            return False
    except Exception as e:
        print(f"✗ Failed to read landing data: {e}")
        return False

    # Wait for Mock DAG processing (S3 event trigger)
    print("Waiting 15s for Mock DAG to process...")
    time.sleep(15)

    # Verify processed output exists
    processed_key = s3_key.replace("ingested/", "processed/")
    try:
        obj = s3_client.get_object(Bucket=stack_outputs["ProcessedBucketName"], Key=processed_key)
        processed_data = json.loads(obj["Body"].read())

        # Verify transformation occurred
        has_amount_cents = "amount_cents" in processed_data.get("data", [{}])[0]
        print(f"✓ Processed output exists: {processed_key}")
        print(f"✓ Transformation added amount_cents: {has_amount_cents}")

        if not has_amount_cents:
            print("✗ TEST FAILED: Transform should add amount_cents")
            return False

        print("✓ HAPPY PATH PASSED")
        return True

    except s3_client.exceptions.NoSuchKey:
        print(f"✗ HAPPY PATH FAILED: No processed output at {processed_key}")
        return False
    except Exception as e:
        print(f"✗ HAPPY PATH FAILED: {e}")
        return False


def test_failure_path(stack_outputs: dict) -> bool:
    """Test 2: Failure path with schema mismatch."""
    print("\n" + "=" * 60)
    print("TEST 2: Failure Path")
    print("=" * 60)

    # Inject schema change
    try:
        requests.post(
            f"{stack_outputs['MockApiUrl']}/config",
            json={"inject_schema_change": True},
            timeout=10,
        )
        print("✓ Configured Mock API (schema change injected)")
    except Exception as e:
        print(f"✗ Failed to configure Mock API: {e}")
        return False

    # Verify API returns bad data
    api_data = requests.get(f"{stack_outputs['MockApiUrl']}/data", timeout=10).json()
    has_customer_id = "customer_id" in api_data.get("data", [{}])[0]
    print(f"✓ Mock API returns data WITHOUT customer_id: {not has_customer_id}")
    if has_customer_id:
        print("✗ TEST FAILED: Mock API should NOT have customer_id")
        return False

    # Invoke ingester
    correlation_id = f"fail-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
    lambda_client = boto3.client("lambda")

    print(f"Invoking Ingester Lambda (correlation_id={correlation_id})...")
    response = lambda_client.invoke(
        FunctionName=stack_outputs["IngesterFunctionName"],
        Payload=json.dumps({"correlation_id": correlation_id}),
    )

    result = json.loads(response["Payload"].read())
    s3_key = result["s3_key"]
    print(f"✓ Bad data written to landing: {s3_key}")

    # Verify landing bucket has bad data
    s3_client = boto3.client("s3")
    try:
        obj = s3_client.get_object(Bucket=stack_outputs["LandingBucketName"], Key=s3_key)
        landing_data = json.loads(obj["Body"].read())
        has_customer_id = "customer_id" in landing_data.get("data", [{}])[0]
        print(f"✓ Landing data missing customer_id: {not has_customer_id}")

        if has_customer_id:
            print("✗ TEST FAILED: Landing data should NOT have customer_id")
            return False
    except Exception as e:
        print(f"✗ Failed to read landing data: {e}")
        return False

    # Wait for Mock DAG processing
    print("Waiting 15s for Mock DAG to process and fail...")
    time.sleep(15)

    # Verify NO processed output (pipeline should have failed)
    processed_key = s3_key.replace("ingested/", "processed/")
    try:
        s3_client.head_object(Bucket=stack_outputs["ProcessedBucketName"], Key=processed_key)
        print(f"✗ FAILURE PATH FAILED: Output exists at {processed_key} (should have failed)")
        return False
    except Exception as e:
        # Should fail (object doesn't exist)
        if "404" in str(e) or "NoSuchKey" in str(e):
            print("✓ No processed output (correctly failed)")
        else:
            print(f"✗ Unexpected error checking processed output: {e}")
            return False

    # Verify error in CloudWatch logs
    logs_client = boto3.client("logs")
    log_group = f"/aws/lambda/{stack_outputs['MockDagFunctionName']}"

    try:
        response = logs_client.filter_log_events(
            logGroupName=log_group,
            startTime=int((time.time() - 300) * 1000),
            filterPattern="Schema validation failed",
        )

        if response["events"]:
            error_msg = response["events"][0]["message"]
            print("✓ Error logged in CloudWatch:")
            print(f"  {error_msg[:200]}")
            print("✓ FAILURE PATH PASSED")
            return True
        else:
            print("✗ FAILURE PATH FAILED: No error in logs")
            print(f"  Check log group: {log_group}")
            return False
    except Exception as e:
        print(f"✗ Failed to check CloudWatch logs: {e}")
        return False


if __name__ == "__main__":
    # Get stack outputs
    print("Getting stack outputs...")
    stack_outputs = get_stack_outputs()

    # Run tests
    happy_passed = test_happy_path(stack_outputs)
    failure_passed = test_failure_path(stack_outputs)

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"  Happy Path:   {'PASSED' if happy_passed else 'FAILED'}")
    print(f"  Failure Path: {'PASSED' if failure_passed else 'FAILED'}")
    print()

    if all([happy_passed, failure_passed]):
        print("✓ ALL TESTS PASSED")
        sys.exit(0)
    else:
        print("✗ SOME TESTS FAILED")
        sys.exit(1)

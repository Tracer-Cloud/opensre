#!/usr/bin/env python3
"""
Local test for Prefect flow against existing S3 buckets.

This script:
1. Starts a Prefect server in Docker (if not already running)
2. Writes test data to the existing S3 landing bucket
3. Runs the Prefect flow locally
4. Validates the output in the processed bucket

Prerequisites:
- Docker installed and running
- AWS credentials configured
- pip install prefect boto3 requests

Usage:
    python test_local.py              # Run happy path test
    python test_local.py --fail       # Run failure path test (schema error)
    python test_local.py --no-server  # Skip starting Prefect server
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import boto3
import requests

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Configuration (uses existing Lambda stack buckets)
LANDING_BUCKET = "tracerupstreamdownstreamtest-landingbucket23fe90fb-felup0en4mqb"
PROCESSED_BUCKET = "tracerupstreamdownstreamte-processedbucketde59930c-bg5m6jrqoq6v"
PREFECT_SERVER_URL = "http://localhost:4200"

s3_client = boto3.client("s3")


def start_prefect_server() -> bool:
    """Start Prefect server in Docker if not already running."""
    print("Checking Prefect server status...")

    # Check if server is already running
    try:
        r = requests.get(f"{PREFECT_SERVER_URL}/api/health", timeout=2)
        if r.status_code == 200:
            print("Prefect server already running")
            return True
    except requests.exceptions.ConnectionError:
        pass

    print("Starting Prefect server in Docker...")

    # Remove any existing container
    subprocess.run(["docker", "rm", "-f", "prefect-server"], capture_output=True)

    # Start new container
    result = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            "prefect-server",
            "-p",
            "4200:4200",
            "prefecthq/prefect:3-python3.11",
            "prefect",
            "server",
            "start",
            "--host",
            "0.0.0.0",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"Failed to start container: {result.stderr}")
        return False

    # Wait for server to be ready
    print("Waiting for Prefect server to be ready...")
    for i in range(60):
        try:
            r = requests.get(f"{PREFECT_SERVER_URL}/api/health", timeout=2)
            if r.status_code == 200:
                print(f"Prefect server ready (took {i + 1}s)")
                return True
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(1)

    print("Prefect server failed to start within 60s")
    return False


def write_test_data(inject_error: bool = False) -> str:
    """Write test data to S3 landing bucket."""
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    correlation_id = f"local-test-{timestamp}"
    s3_key = f"ingested/{timestamp}/data.json"

    if inject_error:
        # Missing customer_id field - will cause schema validation error
        data = {
            "data": [
                {"order_id": "ORD-001", "amount": 99.99, "timestamp": timestamp},
                {"order_id": "ORD-002", "amount": 149.50, "timestamp": timestamp},
            ],
            "meta": {"schema_version": "2.0", "note": "Missing customer_id"},
        }
    else:
        # Valid data
        data = {
            "data": [
                {
                    "customer_id": "CUST-001",
                    "order_id": "ORD-001",
                    "amount": 99.99,
                    "timestamp": timestamp,
                },
                {
                    "customer_id": "CUST-002",
                    "order_id": "ORD-002",
                    "amount": 149.50,
                    "timestamp": timestamp,
                },
            ],
            "meta": {"schema_version": "1.0"},
        }

    print(f"Writing test data to s3://{LANDING_BUCKET}/{s3_key}")
    s3_client.put_object(
        Bucket=LANDING_BUCKET,
        Key=s3_key,
        Body=json.dumps(data, indent=2),
        ContentType="application/json",
        Metadata={"correlation_id": correlation_id},
    )

    return s3_key


def run_flow(bucket: str, key: str) -> dict:
    """Run the Prefect flow locally."""
    # Import here to avoid import errors before prefect is configured
    from pipeline_code.prefect_flow.flow import data_pipeline_flow

    return data_pipeline_flow(bucket, key)


def verify_output(input_key: str) -> bool:
    """Verify the processed output exists in S3."""
    output_key = input_key.replace("ingested/", "processed/")

    try:
        response = s3_client.get_object(Bucket=PROCESSED_BUCKET, Key=output_key)
        data = json.loads(response["Body"].read().decode())
        record_count = len(data.get("data", []))
        print(f"Output verified: s3://{PROCESSED_BUCKET}/{output_key}")
        print(f"  Records: {record_count}")
        return True
    except s3_client.exceptions.NoSuchKey:
        print(f"Output not found: s3://{PROCESSED_BUCKET}/{output_key}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Local test for Prefect flow")
    parser.add_argument("--fail", action="store_true", help="Inject schema error")
    parser.add_argument("--no-server", action="store_true", help="Skip starting Prefect server")
    args = parser.parse_args()

    print("=" * 60)
    print("Prefect Flow Local Test")
    print("=" * 60)
    print()

    # Configure Prefect to use local server
    import os

    os.environ["PREFECT_API_URL"] = f"{PREFECT_SERVER_URL}/api"

    # Set local OTLP endpoint for telemetry (if Grafana stack is running)
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") is None:
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
        os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "grpc"

    # Step 1: Start Prefect server
    if not args.no_server and not start_prefect_server():
        print("\nFailed to start Prefect server")
        return 1
    print()

    # Step 2: Write test data
    print(f"Test mode: {'FAILURE (schema error)' if args.fail else 'SUCCESS (valid data)'}")
    s3_key = write_test_data(inject_error=args.fail)
    print()

    # Step 3: Run flow
    print("Running Prefect flow...")
    try:
        result = run_flow(LANDING_BUCKET, s3_key)
        print(f"Flow result: {result}")

        if args.fail:
            print("\nERROR: Flow should have failed but succeeded!")
            return 1

        # Step 4: Verify output
        print()
        if verify_output(s3_key):
            print("\n" + "=" * 60)
            print("TEST PASSED")
            print("=" * 60)
            return 0
        else:
            print("\nERROR: Output verification failed")
            return 1

    except Exception as e:
        if args.fail:
            print(f"\nFlow failed as expected: {type(e).__name__}: {e}")
            print("\n" + "=" * 60)
            print("TEST PASSED (failure path)")
            print("=" * 60)
            return 0
        else:
            print(f"\nUnexpected error: {e}")
            import traceback

            traceback.print_exc()
            return 1


if __name__ == "__main__":
    sys.exit(main())

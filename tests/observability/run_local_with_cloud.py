#!/usr/bin/env python3
"""Run local pipelines with Grafana Cloud endpoints.

Wrapper script that:
1. Sets Grafana Cloud environment variables from Secrets Manager
2. Runs each test case pipeline locally
3. Waits for telemetry export
4. Validates data appears in Grafana Cloud
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import boto3


def get_grafana_secrets() -> dict[str, str]:
    """Retrieve Grafana Cloud secrets from AWS Secrets Manager."""
    secrets_client = boto3.client("secretsmanager")
    try:
        response = secrets_client.get_secret_value(SecretId="tracer/grafana-cloud")
        secret_string = response["SecretString"]
        return json.loads(secret_string)
    except Exception as e:
        print(f"Failed to retrieve secrets: {e}")
        print("Falling back to environment variables...")
        return {
            "GCLOUD_HOSTED_METRICS_ID": os.getenv("GCLOUD_HOSTED_METRICS_ID", ""),
            "GCLOUD_HOSTED_METRICS_URL": os.getenv("GCLOUD_HOSTED_METRICS_URL", ""),
            "GCLOUD_HOSTED_LOGS_ID": os.getenv("GCLOUD_HOSTED_LOGS_ID", ""),
            "GCLOUD_HOSTED_LOGS_URL": os.getenv("GCLOUD_HOSTED_LOGS_URL", ""),
            "GCLOUD_RW_API_KEY": os.getenv("GCLOUD_RW_API_KEY", ""),
            "GCLOUD_OTLP_ENDPOINT": os.getenv("GCLOUD_OTLP_ENDPOINT", ""),
            "GCLOUD_OTLP_AUTH_HEADER": os.getenv("GCLOUD_OTLP_AUTH_HEADER", ""),
        }


def run_command(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Run a shell command and return exit code, stdout, stderr."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, env=env, check=False
    )
    return result.returncode, result.stdout, result.stderr


def run_prefect_flow(secrets: dict[str, str]) -> bool:
    """Run Prefect flow locally with Grafana Cloud endpoints."""
    print("Running Prefect flow locally with Grafana Cloud endpoints...")
    prefect_dir = Path(__file__).parent.parent / "test_case_upstream_prefect_ecs_fargate"
    flow_file = prefect_dir / "pipeline_code" / "prefect_flow" / "flow.py"

    env = os.environ.copy()
    env.update(secrets)
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = secrets.get("GCLOUD_OTLP_ENDPOINT", "")
    env["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization={secrets.get('GCLOUD_OTLP_AUTH_HEADER', '')}"
    env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "grpc"

    exit_code, stdout, stderr = run_command(
        ["python3", str(flow_file), "test-bucket", "test-key"],
        cwd=prefect_dir,
        env=env,
    )

    if exit_code != 0:
        print(f"Prefect flow failed: {stderr}")
        return False

    print("Prefect flow completed. Waiting for telemetry export...")
    time.sleep(5)
    return True


def main() -> int:
    """Main entry point."""
    print("Retrieving Grafana Cloud credentials...")
    secrets = get_grafana_secrets()

    required_vars = [
        "GCLOUD_OTLP_ENDPOINT",
        "GCLOUD_OTLP_AUTH_HEADER",
    ]

    missing = [var for var in required_vars if not secrets.get(var)]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        return 1

    print("Running test pipelines with Grafana Cloud endpoints...")

    success = True
    success = run_prefect_flow(secrets) and success

    if success:
        print("\n✓ All pipelines completed successfully")
        print("Telemetry should now be available in Grafana Cloud")
        print("Run validate_grafana_cloud.py to verify telemetry ingestion")
    else:
        print("\n✗ Some pipelines failed")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())

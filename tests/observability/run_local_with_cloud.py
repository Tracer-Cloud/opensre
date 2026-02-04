#!/usr/bin/env python3
"""Run local pipelines with Grafana Cloud endpoints.

Wrapper script that:
1. Sets Grafana Cloud environment variables from .env/environment
2. Runs each test case pipeline locally
3. Waits for telemetry export
"""

import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_env():
    """Load .env with a minimal parser to avoid parse warnings."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_grafana_secrets():
    """Retrieve Grafana Cloud secrets from environment variables."""
    _load_env()
    return {
        "GCLOUD_HOSTED_METRICS_ID": os.getenv("GCLOUD_HOSTED_METRICS_ID", ""),
        "GCLOUD_HOSTED_METRICS_URL": os.getenv("GCLOUD_HOSTED_METRICS_URL", ""),
        "GCLOUD_HOSTED_LOGS_ID": os.getenv("GCLOUD_HOSTED_LOGS_ID", ""),
        "GCLOUD_HOSTED_LOGS_URL": os.getenv("GCLOUD_HOSTED_LOGS_URL", ""),
        "GCLOUD_HOSTED_TRACES_ID": os.getenv("GCLOUD_HOSTED_TRACES_ID", ""),
        "GCLOUD_HOSTED_TRACES_URL": os.getenv("GCLOUD_HOSTED_TRACES_URL", ""),
        "GCLOUD_HOSTED_TRACES_URL_TEMPO": os.getenv(
            "GCLOUD_HOSTED_TRACES_URL_TEMPO", ""
        ),
        "GCLOUD_RW_API_KEY": os.getenv("GCLOUD_RW_API_KEY", ""),
        "GCLOUD_OTLP_ENDPOINT": os.getenv("GCLOUD_OTLP_ENDPOINT", ""),
        "GCLOUD_OTLP_AUTH_HEADER": os.getenv("GCLOUD_OTLP_AUTH_HEADER", ""),
    }


def run_command(cmd, cwd=None, env=None):
    """Run a shell command and return exit code, stdout, stderr."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, env=env, check=False
    )
    return result.returncode, result.stdout, result.stderr


def run_prefect_flow(secrets):
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


def main():
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

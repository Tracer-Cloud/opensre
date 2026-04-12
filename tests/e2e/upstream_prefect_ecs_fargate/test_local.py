#!/usr/bin/env python3
"""
Local test for Prefect ETL flow.

Prerequisites:
    prefect server start  # Run in another terminal

Usage (from project root):
    python -m tests.e2e.upstream_prefect_ecs_fargate.test_local
    python -m tests.e2e.upstream_prefect_ecs_fargate.test_local --fail
    python -m tests.e2e.upstream_prefect_ecs_fargate.test_local --cloud
"""

import argparse
import sys

import requests

from app.services.grafana import get_grafana_client
from tests.shared.stack_config import get_prefect_config
from tests.utils.s3_upload_validate import (
    INVALID_PAYLOAD,
    VALID_PAYLOAD,
    upload_test_data,
    verify_output,
)


def _prefect_base_url(api_url: str) -> str:
    """Normalize Prefect API URL to base server URL."""
    url = api_url.rstrip("/")
    if url.endswith("/api"):
        return url[: -len("/api")]
    return url


def require_prefect_server(api_url: str, timeout: int = 2) -> bool:
    """Check Prefect server is running, print status."""
    base_url = _prefect_base_url(api_url)
    try:
        ok = requests.get(f"{base_url}/api/health", timeout=timeout).ok
    except requests.RequestException:
        ok = False

    if ok:
        print("✓ Prefect server running")
    else:
        print(f"✗ Prefect server not running at {base_url}")
        print("  Run: prefect server start")
    return ok


def run_test(
    landing_bucket: str,
    processed_bucket: str,
    prefect_api_url: str,
    valid_payload: dict,
    invalid_payload: dict,
    expect_failure: bool = False,
) -> tuple[bool, str | None]:
    """Run the full test: check server, upload, execute flow, verify output."""
    from prefect.settings import PREFECT_API_URL, temporary_settings

    from tests.e2e.upstream_prefect_ecs_fargate.pipeline_code.prefect_flow.main_pipeline.main_pipeline import (
        data_pipeline_flow,
    )

    if not require_prefect_server(prefect_api_url):
        return False, None

    payload = invalid_payload if expect_failure else valid_payload
    test_data = upload_test_data(landing_bucket, payload)
    correlation_id = test_data.correlation_id

    try:
        with temporary_settings({PREFECT_API_URL: prefect_api_url}):
            result = data_pipeline_flow(
                landing_bucket,
                test_data.key,
                processed_bucket,
            )
            print(f"✓ Flow completed: {result}")

        if expect_failure:
            print("✗ Flow should have failed")
            return False, correlation_id

        return verify_output(processed_bucket, test_data.key), correlation_id

    except Exception as e:
        if expect_failure:
            print(f"✓ Flow failed as expected: {e}")
            return True, correlation_id

        print(f"✗ Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        return False, correlation_id


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Local Prefect flow test")
    parser.add_argument("--fail", action="store_true", help="Test failure path")
    parser.add_argument("--cloud", action="store_true", help="Use deployed Prefect ECS config")
    args = parser.parse_args()

    config = get_prefect_config()
    landing_bucket = config.get("s3_bucket")
    processed_bucket = config.get("processed_bucket")
    prefect_api_url = config.get("prefect_api_url") if args.cloud else "http://localhost:4200/api"

    required = {
        "s3_bucket": landing_bucket,
        "processed_bucket": processed_bucket,
    }
    if args.cloud:
        required["prefect_api_url"] = prefect_api_url

    missing = [name for name, value in required.items() if not value]
    if missing:
        print("✗ Missing Prefect config values: " + ", ".join(missing))
        print("  Ensure AWS credentials are available and the stack is deployed.")
        return 1

    success, correlation_id = run_test(
        landing_bucket,
        processed_bucket,
        prefect_api_url,
        VALID_PAYLOAD,
        INVALID_PAYLOAD,
        expect_failure=args.fail,
    )

    status = "✓ PASSED" if success else "✗ FAILED"
    print(f"\n{'=' * 60}")
    print(f"TEST {status}")
    print(f"{'=' * 60}\n")

    grafana_client = get_grafana_client()
    log_url = grafana_client.build_loki_explore_url(
        service_name="prefect-etl-pipeline",
        correlation_id=correlation_id,
    )
    print("Grafana Cloud logs (Prefect flow service):")
    if log_url:
        print(f"  {log_url}")
    else:
        print("  (Grafana Cloud instance URL not configured)")
    print("  Paste this log URL after the test run.\n")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())

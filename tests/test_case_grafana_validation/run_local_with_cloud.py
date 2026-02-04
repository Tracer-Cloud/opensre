#!/usr/bin/env python3
"""Prepare local environment for Grafana Cloud OTLP export."""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_env():
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


def main():
    load_env()
    required = [
        "GCLOUD_OTLP_ENDPOINT",
        "GCLOUD_OTLP_AUTH_HEADER",
    ]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        print(f"❌ Missing required environment variables: {', '.join(missing)}")
        return 1

    print("✅ Grafana Cloud OTLP environment is set")
    print()
    print("Run the local Prefect test with the current env:")
    print("  python3 -m tests.test_case_upstream_prefect_ecs_fargate.test_local")
    print()
    print("Then validate Grafana Cloud ingestion:")
    print("  python3 tests/test_case_grafana_validation/validate_grafana_cloud.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())

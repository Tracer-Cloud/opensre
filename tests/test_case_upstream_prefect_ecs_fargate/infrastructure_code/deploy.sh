#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/../.."

echo "=== Prefect ECS Test Case Deployment (SDK) ==="
python3 infrastructure_sdk/deploy.py

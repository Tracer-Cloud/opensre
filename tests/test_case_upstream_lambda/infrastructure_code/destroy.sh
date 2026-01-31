#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$INFRA_DIR/cdk"

echo "=== ECS Fargate Airflow Test Case Destruction ==="
echo "This will destroy all resources including:"
echo "  - ECS Fargate Airflow services"
echo "  - S3 buckets (DAGs and data)"
echo "  - Lambda function"
echo "  - VPC and networking"
echo ""

read -p "Are you sure you want to destroy all resources? (y/N): " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "Destroying ECS Fargate Airflow stack..."
echo "This may take several minutes."
echo ""

cdk destroy --force

echo ""
echo "=== Destruction Complete ==="

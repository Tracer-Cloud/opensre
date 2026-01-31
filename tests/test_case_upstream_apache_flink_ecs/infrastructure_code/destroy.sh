#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/cdk"

echo "=== Apache Flink ECS Stack Destruction ==="
echo ""
echo "WARNING: This will destroy all resources including:"
echo "  - ECS cluster"
echo "  - S3 buckets (landing and processed)"
echo "  - Lambda functions and API Gateway"
echo "  - CloudWatch log groups"
echo ""

read -p "Are you sure you want to destroy all resources? (y/N) " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Destruction cancelled."
    exit 0
fi

echo ""
echo "Destroying CDK stack..."
cdk destroy --force

echo ""
echo "=== Destruction Complete ==="

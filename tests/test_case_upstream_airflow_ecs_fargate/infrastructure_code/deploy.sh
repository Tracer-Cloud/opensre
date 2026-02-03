#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/cdk"

echo "=== Airflow ECS Fargate Deployment ==="
echo ""

if ! command -v cdk &> /dev/null; then
    echo "ERROR: AWS CDK CLI not found. Install with: npm install -g aws-cdk"
    exit 1
fi

if ! command -v aws &> /dev/null; then
    echo "ERROR: AWS CLI not found. Install from: https://aws.amazon.com/cli/"
    exit 1
fi

echo "Verifying AWS credentials..."
AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
if [ -z "$AWS_ACCOUNT" ]; then
    echo "ERROR: Unable to verify AWS credentials. Configure with: aws configure"
    exit 1
fi
echo "Using AWS account: $AWS_ACCOUNT"

echo ""
echo "Installing CDK dependencies..."
python3 -m pip install -r requirements.txt --break-system-packages -q

echo ""
echo "Bootstrapping CDK (if needed)..."
cdk bootstrap --quiet 2>/dev/null || true

echo ""
echo "Deploying CDK stack..."
echo ""

cdk deploy --require-approval never

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Outputs:"
cdk output 2>/dev/null || echo "(Run 'cdk output' to see stack outputs)"
echo ""
echo "Note: The ECS service may take a few minutes to start."
echo "Check CloudWatch logs at /ecs/tracer-airflow for Airflow startup."
echo ""
echo "To update the trigger Lambda with the Airflow API URL:"
echo "1. Get the ECS task public IP from the AWS Console"
echo "2. Update the AIRFLOW_API_URL environment variable in the trigger Lambda"

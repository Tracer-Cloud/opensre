#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Apache Flink ECS Deployment ==="
echo ""

# Copy pipeline code to Docker build context
echo "Copying pipeline code to Docker build context..."
rm -rf "$SCRIPT_DIR/flink_image/flink_job"
cp -r "$SCRIPT_DIR/../pipeline_code/flink_job" "$SCRIPT_DIR/flink_image/flink_job"
echo "Done."
echo ""

cd "$SCRIPT_DIR/cdk"

# Check prerequisites
if ! command -v cdk &> /dev/null; then
    echo "ERROR: AWS CDK CLI not found. Install with: npm install -g aws-cdk"
    exit 1
fi

if ! command -v aws &> /dev/null; then
    echo "ERROR: AWS CLI not found. Install from: https://aws.amazon.com/cli/"
    exit 1
fi

# Verify AWS credentials
echo "Verifying AWS credentials..."
AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
if [ -z "$AWS_ACCOUNT" ]; then
    echo "ERROR: Unable to verify AWS credentials. Configure with: aws configure"
    exit 1
fi
echo "Using AWS account: $AWS_ACCOUNT"

# Install dependencies
echo ""
echo "Installing CDK dependencies..."
python3 -m pip install -r requirements.txt --break-system-packages -q

# Bootstrap CDK (if needed)
echo ""
echo "Bootstrapping CDK (if needed)..."
cdk bootstrap --quiet 2>/dev/null || true

# Deploy
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
echo "To trigger the pipeline:"
echo "  curl -X POST \"\$(cdk output TracerFlinkEcs --output json | jq -r '.TriggerApiUrl')trigger\""
echo ""
echo "To trigger with error injection:"
echo "  curl -X POST \"\$(cdk output TracerFlinkEcs --output json | jq -r '.TriggerApiUrl')trigger?inject_error=true\""

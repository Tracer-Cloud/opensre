#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== MWAA Test Case Deployment ==="
echo "WARNING: MWAA environment creation takes ~25 minutes"
echo ""

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
pip install -r requirements.txt -q

# Bootstrap CDK (if needed)
echo ""
echo "Bootstrapping CDK (if needed)..."
cdk bootstrap --quiet 2>/dev/null || true

# Deploy
echo ""
echo "Deploying MWAA stack..."
echo "This will take approximately 25-30 minutes for MWAA environment creation."
echo ""

cdk deploy --require-approval never

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Outputs:"
cdk output 2>/dev/null || echo "(Run 'cdk output' to see stack outputs)"

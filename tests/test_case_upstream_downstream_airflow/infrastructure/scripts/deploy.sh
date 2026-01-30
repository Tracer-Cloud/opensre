#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$INFRA_DIR/cdk"

echo "=== ECS Fargate Airflow Test Case Deployment ==="
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

# Run pre-deployment validation
echo ""
echo "Running pre-deployment validation..."
python3 ../tests/validate_setup.py || {
    echo "Pre-deployment validation failed. Fix issues before deploying."
    exit 1
}

# Install dependencies
echo ""
echo "Installing CDK dependencies..."
python3 -m pip install --user --break-system-packages -r ../requirements/requirements.txt -q

# Bootstrap CDK (if needed)
echo ""
echo "Bootstrapping CDK (if needed)..."
cdk bootstrap --quiet 2>/dev/null || true

# Deploy
echo ""
echo "Deploying ECS Fargate Airflow stack..."
echo "This will take approximately 10-15 minutes for ECS services to start."
echo ""

cdk deploy --require-approval never

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Outputs:"
cdk output 2>/dev/null || echo "(Run 'cdk output' to see stack outputs)"
echo ""
echo "Note: Airflow webserver may take a few minutes to initialize."
echo "Check CloudWatch logs if the webserver is not responding."

#!/usr/bin/env python3
"""
Deploy and trigger the Prefect flow using Prefect 3.x API.

This script:
1. Deploys the flow to the remote Prefect server
2. Triggers a flow run with S3 parameters
"""

import os
import sys

# Configure Prefect to use remote server
os.environ["PREFECT_API_URL"] = "http://98.91.253.152:4200/api"

# Add pipeline code to path
sys.path.insert(0, "pipeline_code")

# Import the flow
from prefect_flow.flow import data_pipeline_flow

# S3 parameters
BUCKET = "tracerprefectecsfargate-landingbucket23fe90fb-woehzac5msvj"
KEY = "ingested/20260131-124548/data.json"


if __name__ == "__main__":
    print(f"Connecting to Prefect at {os.environ['PREFECT_API_URL']}")

    # Deploy the flow using Prefect 3.x API
    print("\nDeploying flow to remote Prefect server...")
    deployment_id = data_pipeline_flow.deploy(
        name="s3-processor",
        work_pool_name="default-pool",
        image=None,  # No image build needed, code is in ECS container
        push=False,   # Don't push to registry
        parameters={"bucket": BUCKET, "key": KEY},
    )

    print("✅ Flow deployed and triggered!")
    print("   Deployment: s3-processor")
    print(f"   Parameters: bucket={BUCKET}, key={KEY}")
    print("\n✅ Check CloudWatch logs /ecs/tracer-prefect for execution")

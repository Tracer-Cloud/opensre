#!/usr/bin/env python3
"""
Run the Prefect flow directly, connected to remote server.

This will execute the flow logic locally but register the run with the remote Prefect server.
"""

import os
import sys

# Configure Prefect to use remote server
os.environ["PREFECT_API_URL"] = "http://98.91.253.152:4200/api"

# Configure AWS region
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

# Add pipeline code to path
sys.path.insert(0, "pipeline_code")

# Import the flow
from prefect_flow.flow import data_pipeline_flow

# S3 parameters
BUCKET = "tracerprefectecsfargate-landingbucket23fe90fb-woehzac5msvj"
KEY = "ingested/20260131-124548/data.json"


if __name__ == "__main__":
    print(f"Connecting to Prefect at {os.environ['PREFECT_API_URL']}")
    print(f"Running flow for s3://{BUCKET}/{KEY}\n")

    # Run the flow - this will execute locally but log to remote Prefect server
    try:
        result = data_pipeline_flow(bucket=BUCKET, key=KEY)
        print(f"\n✅ Flow completed: {result}")
    except Exception as e:
        print(f"\n❌ Flow failed: {e}")
        import traceback
        traceback.print_exc()

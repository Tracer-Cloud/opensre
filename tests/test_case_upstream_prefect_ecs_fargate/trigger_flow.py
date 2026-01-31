"""
Quick script to trigger the Prefect flow for testing.

This connects to the remote Prefect server and triggers a flow run.
"""

import os
import sys

# Add the pipeline code to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pipeline_code"))

from prefect import get_client
from prefect.client.schemas.actions import DeploymentFlowRunCreate
from prefect.deployments import run_deployment

# Set API URL for remote Prefect server
PREFECT_API_URL = "http://98.91.253.152:4200/api"
os.environ["PREFECT_API_URL"] = PREFECT_API_URL

# S3 location from the trigger response
BUCKET = "tracerprefectecsfargate-landingbucket23fe90fb-woehzac5msvj"
KEY = "ingested/20260131-124548/data.json"


async def trigger_flow():
    """Trigger the flow directly without deployment."""
    from prefect_flow.flow import data_pipeline_flow

    # Run the flow directly (will use local/ephemeral infrastructure)
    result = await data_pipeline_flow(BUCKET, KEY, return_state=True)
    print(f"Flow run completed: {result}")


if __name__ == "__main__":
    import asyncio

    print(f"Connecting to Prefect server at {PREFECT_API_URL}")
    print(f"Triggering flow for s3://{BUCKET}/{KEY}")

    asyncio.run(trigger_flow())

"""Configuration for Prefect flow.

Uses existing S3 buckets from the Lambda stack for local testing.
Update these when deploying new ECS infrastructure.
"""

import os

# S3 Buckets (from existing Lambda stack, or override via env vars)
LANDING_BUCKET = os.getenv(
    "LANDING_BUCKET",
    "tracerupstreamdownstreamtest-landingbucket23fe90fb-felup0en4mqb",
)
PROCESSED_BUCKET = os.getenv(
    "PROCESSED_BUCKET",
    "tracerupstreamdownstreamte-processedbucketde59930c-bg5m6jrqoq6v",
)

# Pipeline Configuration
PIPELINE_NAME = "upstream_downstream_pipeline_prefect"
REQUIRED_FIELDS = ["customer_id", "order_id", "amount", "timestamp"]

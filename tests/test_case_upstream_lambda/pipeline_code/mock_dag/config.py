"""Configuration for Mock DAG Lambda.

Hardcoded values from deployed infrastructure.
Update these when redeploying the stack.
"""

# S3 Buckets
LANDING_BUCKET = "tracerupstreamdownstreamtest-landingbucket23fe90fb-felup0en4mqb"
PROCESSED_BUCKET = "tracerupstreamdownstreamte-processedbucketde59930c-bg5m6jrqoq6v"

# Pipeline Configuration
PIPELINE_NAME = "upstream_downstream_pipeline"
REQUIRED_FIELDS = ["customer_id", "order_id", "amount", "timestamp"]

# AWS Resources
MOCK_DAG_FUNCTION_NAME = "TracerUpstreamDownstreamTest-MockDagLambdaCF347C20-3X8c3pPwK2Bq"
INGESTER_FUNCTION_NAME = "TracerUpstreamDownstreamTes-IngesterLambda519919B4-swSsLumUC0KN"
MOCK_API_URL = "https://pf2u8sbgk7.execute-api.us-east-1.amazonaws.com/prod/"

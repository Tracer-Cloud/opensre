"""Pytest configuration and fixtures for all tests."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Auto-load .env when this module is imported (works for both pytest and direct execution)
_project_root = Path(__file__).parent.parent
_env_path = _project_root / ".env"
if _env_path.exists():
    load_dotenv(dotenv_path=_env_path, override=True)


def pytest_configure(config):
    """Pytest hook - .env already loaded above."""
    pass


def get_test_config() -> dict:
    """Get test configuration (not a pytest fixture - plain function)."""
    return {
        "aws_region": os.getenv("AWS_REGION", "us-east-1"),
        "langgraph_endpoint": os.getenv("LANGGRAPH_ENDPOINT", "http://localhost:8123/runs/stream"),
    }


# LangGraph Studio Endpoints
LANGGRAPH_LOCAL_ENDPOINT = "http://127.0.0.1:2024/runs/stream"
LANGGRAPH_REMOTE_ENDPOINT = (
    "https://tracer-agent-2026-e09h3n0zulnlz1-lwyjk39e.us-central1.run.app/agent/runs/stream"
)

# Upstream/Downstream Pipeline Test Case - AWS Resources
# Stack: TracerUpstreamDownstreamTest
UPSTREAM_DOWNSTREAM_CONFIG = {
    "stack_name": "TracerUpstreamDownstreamTest",
    # HTTP Trigger Endpoint (easy testing)
    "ingester_api_url": "https://ud9ogzmatj.execute-api.us-east-1.amazonaws.com/prod/",
    # Mock External API
    "mock_api_url": "https://pf2u8sbgk7.execute-api.us-east-1.amazonaws.com/prod/",
    # Lambda Functions
    "ingester_function_name": "TracerUpstreamDownstreamTes-IngesterLambda519919B4-swSsLumUC0KN",
    "mock_dag_function_name": "TracerUpstreamDownstreamTest-MockDagLambdaCF347C20-3X8c3pPwK2Bq",
    # S3 Buckets
    "landing_bucket_name": "tracerupstreamdownstreamtest-landingbucket23fe90fb-felup0en4mqb",
    "processed_bucket_name": "tracerupstreamdownstreamte-processedbucketde59930c-bg5m6jrqoq6v",
}

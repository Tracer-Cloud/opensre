"""Pytest configuration and fixtures for all tests."""

import os
from pathlib import Path

from app.utils.config import load_env

# Environment loading
_PROJECT_ROOT = Path(__file__).parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"


def _load_env() -> None:
    if _ENV_PATH.exists():
        load_env(_ENV_PATH, override=True)


# Auto-load .env when this module is imported (pytest and direct execution).
_load_env()


def pytest_configure(config):
    """Pytest hook - keep env available for collection and execution."""
    _load_env()


def get_test_config() -> dict:
    """Get test configuration (not a pytest fixture - plain function)."""
    return {
        "aws_region": os.getenv("AWS_REGION", "us-east-1"),
        "langgraph_endpoint": os.getenv(
            "LANGGRAPH_ENDPOINT",
            "http://localhost:8123/runs/stream",
        ),
    }


# LangGraph Studio endpoints
LANGGRAPH_LOCAL_ENDPOINT = "http://127.0.0.1:2024/runs/stream"
LANGGRAPH_REMOTE_ENDPOINT = (
    "https://tracer-agent-2026-e09h3n0zulnlz1-lwyjk39e.us-central1.run.app/agent/runs/stream"
)


# Upstream/Downstream Pipeline test case - AWS resources
def _load_upstream_downstream_config() -> dict:
    """Load config from SDK outputs or use CDK fallback."""
    try:
        from tests.shared.infrastructure_sdk.config import load_outputs

        outputs = load_outputs("tracer-lambda")
        return {
            "stack_name": "tracer-lambda",
            "ingester_api_url": outputs["IngesterApiUrl"],
            "mock_api_url": outputs["MockApiUrl"],
            "ingester_function_name": outputs["IngesterFunctionName"],
            "mock_dag_function_name": outputs["MockDagFunctionName"],
            "landing_bucket_name": outputs["LandingBucketName"],
            "processed_bucket_name": outputs["ProcessedBucketName"],
        }
    except (FileNotFoundError, ImportError):
        return {
            "stack_name": "TracerUpstreamDownstreamTest",
            "ingester_api_url": "https://ud9ogzmatj.execute-api.us-east-1.amazonaws.com/prod/",
            "mock_api_url": "https://pf2u8sbgk7.execute-api.us-east-1.amazonaws.com/prod/",
            "ingester_function_name": "TracerUpstreamDownstreamTes-IngesterLambda519919B4-swSsLumUC0KN",
            "mock_dag_function_name": "TracerUpstreamDownstreamTest-MockDagLambdaCF347C20-3X8c3pPwK2Bq",
            "landing_bucket_name": "tracerupstreamdownstreamtest-landingbucket23fe90fb-felup0en4mqb",
            "processed_bucket_name": "tracerupstreamdownstreamte-processedbucketde59930c-bg5m6jrqoq6v",
        }


UPSTREAM_DOWNSTREAM_CONFIG = _load_upstream_downstream_config()


# Prefect ECS Fargate test case - AWS resources
PREFECT_ECS_FARGATE_CONFIG = {
    "stack_name": "TracerPrefectEcsFargate",
    "trigger_api_url": "https://q5tl03u98c.execute-api.us-east-1.amazonaws.com/prod/",
    "ecs_cluster_name": "tracer-prefect-cluster",
    "log_group_name": "/ecs/tracer-prefect",
    "trigger_lambda_name": "TracerPrefectEcsFargate-TriggerLambda2FDB819B-YCP5yvOvuE0l",
    "landing_bucket_name": "tracerprefectecsfargate-landingbucket23fe90fb-woehzac5msvj",
    "processed_bucket_name": "tracerprefectecsfargate-processedbucketde59930c-xwdkeidp0qsu",
}

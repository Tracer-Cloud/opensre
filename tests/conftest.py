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

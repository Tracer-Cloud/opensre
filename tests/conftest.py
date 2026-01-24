"""Pytest configuration and fixtures for all tests."""

from pathlib import Path

from dotenv import load_dotenv


def pytest_configure(config):
    """Load .env file from project root before running any tests."""
    # Find project root (parent of tests directory)
    project_root = Path(__file__).parent.parent
    env_path = project_root / ".env"

    # Load .env file if it exists
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)

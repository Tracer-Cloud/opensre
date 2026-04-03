"""Tests for the production Dockerfile.

These tests validate that the Dockerfile at the repo root is correctly
structured for production deployment.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def dockerfile_path() -> Path:
    """Return the path to the root Dockerfile."""
    return Path(__file__).parent.parent / "Dockerfile"


def test_dockerfile_exists(dockerfile_path: Path) -> None:
    """The Dockerfile must exist at the repo root."""
    assert dockerfile_path.exists(), "Dockerfile not found at repo root"
    assert dockerfile_path.is_file(), "Dockerfile is not a file"


def test_dockerfile_has_python_base(dockerfile_path: Path) -> None:
    """The Dockerfile must use Python 3.11+ as base image."""
    content = dockerfile_path.read_text()
    assert "FROM python:3.11" in content, "Should use Python 3.11+ base image"


def test_dockerfile_installs_dependencies(dockerfile_path: Path) -> None:
    """The Dockerfile must install dependencies from pyproject.toml."""
    content = dockerfile_path.read_text()
    assert "pyproject.toml" in content, "Should copy pyproject.toml"
    assert 'pip install -e "."' in content or "pip install -e ." in content, (
        "Should install package from pyproject.toml"
    )


def test_dockerfile_installs_langgraph_cli(dockerfile_path: Path) -> None:
    """The Dockerfile must install langgraph-cli for the server."""
    content = dockerfile_path.read_text()
    assert "langgraph-cli" in content, "Should install langgraph-cli"


def test_dockerfile_exposes_port_2024(dockerfile_path: Path) -> None:
    """The Dockerfile must expose port 2024 for the LangGraph API."""
    content = dockerfile_path.read_text()
    assert "EXPOSE 2024" in content, "Should expose port 2024"


def test_dockerfile_has_healthcheck(dockerfile_path: Path) -> None:
    """The Dockerfile must have a HEALTHCHECK instruction."""
    content = dockerfile_path.read_text()
    assert "HEALTHCHECK" in content, "Should have HEALTHCHECK instruction"


def test_dockerfile_healthcheck_uses_ok_endpoint(dockerfile_path: Path) -> None:
    """The health check should verify the /ok endpoint is accessible."""
    content = dockerfile_path.read_text()
    assert ":2024/ok" in content, "Should check /ok endpoint for health"


def test_dockerfile_copies_app_code(dockerfile_path: Path) -> None:
    """The Dockerfile must copy the application code."""
    content = dockerfile_path.read_text()
    assert "COPY app/" in content, "Should copy app directory"


def test_dockerfile_copies_langgraph_config(dockerfile_path: Path) -> None:
    """The Dockerfile must copy the langgraph.json configuration."""
    content = dockerfile_path.read_text()
    assert "COPY langgraph.json" in content, "Should copy langgraph.json"


def test_dockerfile_uses_non_root_user(dockerfile_path: Path) -> None:
    """The Dockerfile should run as non-root user for security."""
    content = dockerfile_path.read_text()
    assert "USER" in content, "Should set a non-root USER"


def test_dockerfile_has_cmd_to_start_server(dockerfile_path: Path) -> None:
    """The Dockerfile must have a CMD to start the LangGraph server."""
    content = dockerfile_path.read_text()
    assert "CMD" in content, "Should have CMD instruction"
    assert "langgraph" in content.lower(), "CMD should start langgraph"

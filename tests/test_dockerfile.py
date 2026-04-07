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


def test_dockerfile_uses_langgraph_api_base(dockerfile_path: Path) -> None:
    """The Dockerfile must use the production LangGraph API base image."""
    content = dockerfile_path.read_text()
    assert "FROM langchain/langgraph-api:3.11" in content, (
        "Should use the production LangGraph API base image"
    )


def test_dockerfile_installs_dependencies(dockerfile_path: Path) -> None:
    """The Dockerfile must install the package without editable mode."""
    content = dockerfile_path.read_text()
    assert "pip install" in content, "Should install the package"
    assert "/deps/agent" in content, "Should install from the copied application path"
    assert "pip install -e" not in content, "Should avoid editable installs in production"


def test_dockerfile_configures_langgraph_runtime(dockerfile_path: Path) -> None:
    """The Dockerfile must configure graph loading for the API server."""
    content = dockerfile_path.read_text()
    assert "LANGSERVE_GRAPHS" in content, "Should configure graph loading"
    assert "LANGGRAPH_AUTH" not in content, (
        "Should avoid enterprise-only custom auth in the self-hosted Railway image"
    )


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
    assert "ADD . /deps/agent" in content, "Should add the repository into the image"


def test_dockerfile_copies_langgraph_config(dockerfile_path: Path) -> None:
    """The Dockerfile must point the API server at the application graph."""
    content = dockerfile_path.read_text()
    assert "app/graph_pipeline.py:build_graph" in content, (
        "Should configure the graph entrypoint"
    )


def test_dockerfile_keeps_base_runtime_intact(dockerfile_path: Path) -> None:
    """The Dockerfile should not re-install or shadow the bundled API runtime."""
    content = dockerfile_path.read_text()
    assert "pip install --no-cache-dir --no-deps /api" not in content, (
        "Should rely on the base LangGraph runtime instead of re-installing /api"
    )
    assert "touch /api/langgraph_api/__init__.py" not in content, (
        "Should not mutate the bundled runtime package layout"
    )


def test_dockerfile_has_cmd_to_start_server(dockerfile_path: Path) -> None:
    """The Dockerfile must not start the development server in production."""
    content = dockerfile_path.read_text()
    assert "langgraph dev" not in content, "Should not use the LangGraph dev server"

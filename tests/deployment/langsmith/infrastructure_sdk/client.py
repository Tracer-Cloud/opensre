"""LangSmith/LangGraph Cloud deployment client.

Uses the `langgraph` CLI for deployment and HTTP requests for verification.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_DEPLOYMENT_NAME = "opensre-deploy-test"
HEALTH_POLL_INTERVAL = 15
HEALTH_MAX_ATTEMPTS = 40


def check_prerequisites() -> dict[str, bool]:
    """Check that required tools and credentials are available."""
    return {
        "api_key": bool(
            os.getenv("LANGSMITH_API_KEY")
            or os.getenv("LANGGRAPH_HOST_API_KEY")
            or os.getenv("LANGCHAIN_API_KEY")
        ),
        "langgraph_cli": shutil.which("langgraph") is not None,
        "docker": shutil.which("docker") is not None,
    }


def get_api_key() -> str:
    """Get the LangSmith API key from environment."""
    for var in ("LANGSMITH_API_KEY", "LANGGRAPH_HOST_API_KEY", "LANGCHAIN_API_KEY"):
        value = os.getenv(var)
        if value:
            return value
    raise ValueError("No LangSmith API key found. Set LANGSMITH_API_KEY in your environment.")


def get_deployment_name() -> str:
    """Get deployment name from environment or default."""
    return os.getenv("LANGSMITH_DEPLOYMENT_NAME", DEFAULT_DEPLOYMENT_NAME)


def deploy_langgraph(timeout: int = 600) -> dict[str, str]:
    """Deploy the agent to LangSmith using ``langgraph deploy``.

    Returns:
        Dict with deployment info: DeploymentName, DeploymentUrl.
    """
    name = get_deployment_name()
    logger.info("Deploying to LangSmith as '%s'...", name)

    result = subprocess.run(
        ["langgraph", "deploy"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        logger.error(
            "langgraph deploy failed:\nstdout: %s\nstderr: %s", result.stdout, result.stderr
        )
        raise RuntimeError(
            f"langgraph deploy failed (exit {result.returncode}): {result.stderr[:500]}"
        )

    deployment_url = _parse_deployment_url(result.stdout + "\n" + result.stderr)
    if not deployment_url:
        deployment_url = f"https://{name}.default.us.langgraph.app"

    logger.info("Deployment URL: %s", deployment_url)
    return {"DeploymentName": name, "DeploymentUrl": deployment_url}


def _parse_deployment_url(output: str) -> str | None:
    """Parse deployment URL from langgraph deploy CLI output."""
    for line in output.splitlines():
        stripped = line.strip()
        if "langgraph.app" in stripped or "langchain.app" in stripped:
            for token in stripped.split():
                if token.startswith("http"):
                    return token.rstrip("/")
    return None


def wait_for_health(
    url: str,
    max_attempts: int = HEALTH_MAX_ATTEMPTS,
    api_key: str | None = None,
) -> bool:
    """Wait for the deployment to become healthy.

    Raises:
        TimeoutError: If health check doesn't pass within the timeout.
    """
    health_url = f"{url.rstrip('/')}/ok"
    headers: dict[str, str] = {}
    if api_key:
        headers["x-api-key"] = api_key

    for attempt in range(max_attempts):
        try:
            resp = requests.get(health_url, timeout=10, headers=headers)
            if resp.status_code == 200:
                logger.info("Deployment healthy after %d attempts", attempt + 1)
                return True
            logger.debug("Health check returned %d", resp.status_code)
        except requests.exceptions.RequestException as exc:
            logger.debug("Health check attempt %d failed: %s", attempt + 1, exc)

        if attempt < max_attempts - 1:
            time.sleep(HEALTH_POLL_INTERVAL)

    raise TimeoutError(
        f"Deployment at {url} not healthy after {max_attempts * HEALTH_POLL_INTERVAL}s"
    )


def invoke_agent(
    url: str,
    input_text: str,
    api_key: str | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    """Invoke the deployed agent via the LangGraph runs/stream endpoint.

    Returns:
        Dict with status_code and response content.
    """
    api_key = api_key or get_api_key()
    endpoint = f"{url.rstrip('/')}/runs/stream"

    payload: dict[str, Any] = {
        "input": {
            "alert_name": "deployment-test-alert",
            "pipeline_name": "test-pipeline",
            "severity": "warning",
            "raw_alert": {"message": input_text},
        },
        "config": {"metadata": {}},
        "stream_mode": ["values"],
    }

    headers = {"x-api-key": api_key, "Content-Type": "application/json"}

    resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout, stream=True)
    resp.raise_for_status()

    content = resp.text
    return {"status_code": resp.status_code, "content": content}

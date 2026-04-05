"""Vercel deployment client using the Vercel REST API.

Creates a minimal Python serverless function deployment to validate the
Vercel deployment pipeline. Uses the Vercel API v13 for deployments.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

VERCEL_API_BASE = "https://api.vercel.com"
DEPLOY_POLL_INTERVAL = 10
DEPLOY_MAX_ATTEMPTS = 60

HEALTH_HANDLER_SOURCE = """\
from http.server import BaseHTTPRequestHandler
import json

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body = json.dumps({"status": "ok", "service": "opensre", "deployment": "vercel"})
        self.wfile.write(body.encode())
"""


def check_prerequisites() -> dict[str, bool]:
    """Check that required credentials are available."""
    return {
        "api_token": bool(os.getenv("VERCEL_API_TOKEN")),
    }


def get_api_token() -> str:
    """Get the Vercel API token from environment."""
    token = os.getenv("VERCEL_API_TOKEN")
    if not token:
        raise ValueError("VERCEL_API_TOKEN not set. Get one from https://vercel.com/account/tokens")
    return token


def _get_team_param() -> dict[str, str]:
    """Get optional teamId query parameter."""
    team_id = os.getenv("VERCEL_TEAM_ID")
    if team_id:
        return {"teamId": team_id}
    return {}


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_api_token()}",
        "Content-Type": "application/json",
    }


def create_deployment(
    project_name: str = "opensre-deploy-test",
) -> dict[str, str]:
    """Create a Vercel deployment with a minimal health-check serverless function.

    Returns:
        Dict with DeploymentId, DeploymentUrl, ProjectName.
    """
    logger.info("Creating Vercel deployment for project '%s'...", project_name)

    payload: dict[str, Any] = {
        "name": project_name,
        "files": [
            {
                "file": "api/health.py",
                "data": HEALTH_HANDLER_SOURCE,
            },
            {
                "file": "vercel.json",
                "data": '{"version": 2, "builds": [{"src": "api/health.py", "use": "@vercel/python"}]}',
            },
        ],
        "projectSettings": {
            "framework": None,
        },
    }

    params = _get_team_param()

    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{VERCEL_API_BASE}/v13/deployments",
            headers=_headers(),
            json=payload,
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

    deployment_id = data["id"]
    deployment_url = f"https://{data['url']}"

    logger.info("Deployment created: id=%s url=%s", deployment_id, deployment_url)

    return {
        "DeploymentId": deployment_id,
        "DeploymentUrl": deployment_url,
        "ProjectName": project_name,
    }


def wait_for_deployment(
    deployment_id: str,
    max_attempts: int = DEPLOY_MAX_ATTEMPTS,
) -> str:
    """Wait for a Vercel deployment to reach READY state.

    Returns:
        The deployment state (should be "READY").

    Raises:
        TimeoutError: If deployment doesn't become ready.
        RuntimeError: If deployment fails.
    """
    params = _get_team_param()

    for attempt in range(max_attempts):
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                f"{VERCEL_API_BASE}/v13/deployments/{deployment_id}",
                headers=_headers(),
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

        state = data.get("readyState", data.get("state", "UNKNOWN"))
        logger.debug("Deployment %s state: %s (attempt %d)", deployment_id, state, attempt + 1)

        if state == "READY":
            logger.info("Deployment %s is READY after %d attempts", deployment_id, attempt + 1)
            return state

        if state in ("ERROR", "CANCELED"):
            raise RuntimeError(f"Deployment {deployment_id} entered state {state}")

        if attempt < max_attempts - 1:
            time.sleep(DEPLOY_POLL_INTERVAL)

    raise TimeoutError(
        f"Deployment {deployment_id} not ready after {max_attempts * DEPLOY_POLL_INTERVAL}s"
    )


def check_health(deployment_url: str) -> dict[str, Any]:
    """Hit the deployed health endpoint and return the response.

    Returns:
        Dict with status_code and body.
    """
    url = f"{deployment_url.rstrip('/')}/api/health"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url)
    return {"status_code": resp.status_code, "body": resp.text}


def delete_deployment(deployment_id: str) -> None:
    """Delete a Vercel deployment."""
    logger.info("Deleting Vercel deployment %s...", deployment_id)
    params = _get_team_param()

    with httpx.Client(timeout=30) as client:
        resp = client.delete(
            f"{VERCEL_API_BASE}/v13/deployments/{deployment_id}",
            headers=_headers(),
            params=params,
        )
        if resp.status_code == 404:
            logger.warning("Deployment %s already deleted", deployment_id)
            return
        resp.raise_for_status()

    logger.info("Deployment %s deleted", deployment_id)

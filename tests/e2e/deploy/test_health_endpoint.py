from __future__ import annotations

import json
import socket
import subprocess
import time
import uuid

import requests

from app.deployment.health import poll_deployment_health


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_container_startup_and_health_endpoint_available(deploy_image_tag: str) -> None:
    host_port = _find_free_port()
    container_name = f"opensre-deploy-health-{uuid.uuid4().hex[:8]}"
    run = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            container_name,
            "-p",
            f"{host_port}:2024",
            deploy_image_tag,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr

    base_url = f"http://127.0.0.1:{host_port}"

    try:
        status = poll_deployment_health(
            base_url,
            interval_seconds=2.0,
            max_attempts=60,
            request_timeout_seconds=2.0,
        )
        assert status.status_code == 200

        response = requests.get(f"{base_url}/ok", timeout=5)
        assert response.status_code == 200
        body = json.loads(response.text)
        assert body.get("ok") is True
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            check=False,
            capture_output=True,
            text=True,
        )


def test_health_poll_timeout_for_unhealthy_target() -> None:
    dead_port = _find_free_port()
    time.sleep(0.05)
    try:
        poll_deployment_health(
            f"http://127.0.0.1:{dead_port}",
            interval_seconds=0.1,
            max_attempts=3,
            request_timeout_seconds=0.1,
        )
    except TimeoutError as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("Expected TimeoutError")

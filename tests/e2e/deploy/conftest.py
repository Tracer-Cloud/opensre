from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return result.returncode == 0


def docker_validation_enabled() -> bool:
    return os.getenv("RUN_DEPLOY_DOCKER_TESTS", "").strip() == "1"


@pytest.fixture(scope="session")
def deploy_image_tag() -> str:
    if not docker_validation_enabled():
        pytest.skip("Set RUN_DEPLOY_DOCKER_TESTS=1 to run Docker deployment validation tests")
    if not docker_available():
        pytest.skip("Docker is required for deployment validation tests")

    image_tag = f"opensre-deploy-validate:{uuid.uuid4().hex[:12]}"
    subprocess.run(
        ["docker", "build", "-t", image_tag, "."],
        cwd=str(_repo_root()),
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        yield image_tag
    finally:
        subprocess.run(
            ["docker", "image", "rm", "-f", image_tag],
            check=False,
            capture_output=True,
            text=True,
        )

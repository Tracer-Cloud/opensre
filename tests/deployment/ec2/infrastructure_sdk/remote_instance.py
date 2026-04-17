"""EC2 user-data and helpers for the full remote OpenSRE deployment.

Installs Python, clones the repo, sets up a venv, writes ``.env``, and
starts the lightweight FastAPI investigation server as a systemd service.
"""

from __future__ import annotations

import base64
import logging

from app.deployment.health import poll_deployment_health
from tests.shared.infrastructure_sdk.deployer import (
    DEFAULT_REGION,
    get_boto3_client,
)

logger = logging.getLogger(__name__)

INSTANCE_TYPE = "t3.medium"
SERVER_PORT = 8080
REPO_URL = "https://github.com/Tracer-Cloud/opensre.git"

HEALTH_POLL_INTERVAL = 10
HEALTH_MAX_ATTEMPTS = 60  # 10 min ceiling (pip install can be slow)


def get_latest_al2023_ami(region: str = DEFAULT_REGION) -> str:
    """Look up the latest Amazon Linux 2023 x86_64 AMI via SSM."""
    ssm = get_boto3_client("ssm", region)
    resp = ssm.get_parameter(
        Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
    )
    return str(resp["Parameter"]["Value"])


def generate_remote_user_data(
    env_vars: dict[str, str],
    branch: str = "main",
) -> str:
    """Cloud-init bash script that bootstraps the full OpenSRE server.

    1. Installs Python 3.11, pip, git, make
    2. Clones the repo (specific branch)
    3. Creates a venv, installs the package
    4. Writes ``.env`` from *env_vars*
    5. Creates a systemd unit and starts the server
    """
    env_lines = "\n".join(f"{k}={v}" for k, v in env_vars.items())
    env_b64 = base64.b64encode(env_lines.encode()).decode()

    return f"""\
#!/bin/bash
exec > /var/log/opensre-remote.log 2>&1
set -euo pipefail

echo "=== Installing system dependencies ==="
dnf install -y python3.12 python3.12-pip git make 2>/dev/null || dnf install -y python3.11 python3.11-pip git make

# Pick whichever Python is available (3.12 preferred for PEP 695 type syntax)
if command -v python3.12 &>/dev/null; then
  PYTHON=python3.12
else
  PYTHON=python3.11
fi
echo "Using $PYTHON"

echo "=== Cloning repository ==="
git clone --branch {branch} --single-branch {REPO_URL} /opt/opensre

echo "=== Setting up Python venv ==="
cd /opt/opensre
$PYTHON -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .

echo "=== Writing .env ==="
echo '{env_b64}' | base64 -d > /opt/opensre/.env

echo "=== Creating investigations directory ==="
mkdir -p /opt/opensre/investigations

echo "=== Creating systemd unit ==="
cat > /etc/systemd/system/opensre.service << 'UNITEOF'
[Unit]
Description=OpenSRE Remote Investigation Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/opensre
ExecStart=/opt/opensre/.venv/bin/uvicorn app.remote.server:app --host 0.0.0.0 --port {SERVER_PORT}
Restart=on-failure
RestartSec=5
Environment=PATH=/opt/opensre/.venv/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
UNITEOF

echo "=== Starting service ==="
systemctl daemon-reload
systemctl enable opensre
systemctl start opensre

echo "=== Deployment complete ==="
"""


def wait_for_remote_health(
    public_ip: str,
    port: int = SERVER_PORT,
    max_attempts: int = HEALTH_MAX_ATTEMPTS,
) -> None:
    """Poll ``GET /ok`` until the investigation server responds.

    Raises:
        TimeoutError: If the server doesn't respond in time.
    """
    base_url = f"http://{public_ip}:{port}"
    status = poll_deployment_health(
        base_url,
        interval_seconds=HEALTH_POLL_INTERVAL,
        max_attempts=max_attempts,
        request_timeout_seconds=5.0,
    )
    logger.info("Remote server healthy after %d attempts via %s", status.attempts, status.url)

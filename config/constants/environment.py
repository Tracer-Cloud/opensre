"""Deployment environment variable names."""

from __future__ import annotations

DEPLOYMENT_ENV_ENV = "ENV"
#: Pid of the container supervisor. Set only for processes it starts. ``opensre update``
#: signals that pid after a successful install so the running mode reloads the new binary.
CONTAINER_SUPERVISOR_PID_ENV = "OPENSRE_SUPERVISOR_PID"

__all__ = ["CONTAINER_SUPERVISOR_PID_ENV", "DEPLOYMENT_ENV_ENV"]

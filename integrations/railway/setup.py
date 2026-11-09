"""What Railway needs before it is considered configured.

Every field is individually optional: the Railway CLI can be pre-authenticated
interactively (no token), and project/service/environment can be supplied per
tool call instead of as defaults. What is *not* optional is the combination — a
token **or** a complete default project/service/environment.
:func:`integrations.railway.verifier.verify_railway` is that either/or rule, so
setup and verify agree on "configured" (see integrations/AGENTS.md).

``railway_path`` and ``environment`` carry defaults so pressing enter lands on
the same credentials a non-prompting surface would persist.
"""

from __future__ import annotations

from config.constants.railway import (
    RAILWAY_ENVIRONMENT_ENV,
    RAILWAY_PATH_ENV,
    RAILWAY_PROJECT_ENV,
    RAILWAY_SERVICE_ENV,
    RAILWAY_TOKEN_ENV,
)
from integrations.railway.verifier import verify_railway
from integrations.setup_flow import IntegrationSetupSpec, SetupField

TOKEN_FIELD = "token"
PROJECT_FIELD = "project"
SERVICE_FIELD = "service"
ENVIRONMENT_FIELD = "environment"
RAILWAY_PATH_FIELD = "railway_path"

RAILWAY_SETUP = IntegrationSetupSpec(
    service="railway",
    fields=(
        SetupField(
            name=TOKEN_FIELD,
            label="Railway token",
            prompt="Railway token (optional when the Railway CLI is already logged in)",
            env_var=RAILWAY_TOKEN_ENV,
            required=False,
            secret=True,
        ),
        SetupField(
            name=PROJECT_FIELD,
            label="Railway project",
            prompt="Default Railway project ID or name",
            env_var=RAILWAY_PROJECT_ENV,
            required=False,
        ),
        SetupField(
            name=SERVICE_FIELD,
            label="Railway service",
            prompt="Default Railway service ID or name",
            env_var=RAILWAY_SERVICE_ENV,
            required=False,
        ),
        SetupField(
            name=ENVIRONMENT_FIELD,
            label="Railway environment",
            prompt="Default Railway environment",
            env_var=RAILWAY_ENVIRONMENT_ENV,
            default="production",
        ),
        SetupField(
            name=RAILWAY_PATH_FIELD,
            label="Railway CLI path",
            env_var=RAILWAY_PATH_ENV,
            default="railway",
        ),
    ),
    verify=verify_railway,
)

__all__ = [
    "ENVIRONMENT_FIELD",
    "PROJECT_FIELD",
    "RAILWAY_PATH_FIELD",
    "RAILWAY_SETUP",
    "SERVICE_FIELD",
    "TOKEN_FIELD",
]

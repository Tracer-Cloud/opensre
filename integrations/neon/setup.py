"""What Neon needs before it is considered configured.

An API key and a project ID are required. ``branch_id`` is optional: left
blank, the tools follow the project's default branch.
"""

from __future__ import annotations

from config.constants.neon import NEON_API_KEY_ENV, NEON_BRANCH_ID_ENV, NEON_PROJECT_ID_ENV
from integrations.neon.verifier import verify_neon
from integrations.setup_flow import IntegrationSetupSpec, SetupField

API_KEY_FIELD = "api_key"
PROJECT_ID_FIELD = "project_id"
BRANCH_ID_FIELD = "branch_id"

NEON_SETUP = IntegrationSetupSpec(
    service="neon",
    fields=(
        SetupField(
            name=API_KEY_FIELD,
            label="Neon API key",
            prompt="Neon API key (personal, organization, or project-scoped)",
            env_var=NEON_API_KEY_ENV,
            secret=True,
        ),
        SetupField(
            name=PROJECT_ID_FIELD,
            label="Neon project ID",
            prompt="Neon project ID (e.g. little-lab-10567887)",
            env_var=NEON_PROJECT_ID_ENV,
        ),
        SetupField(
            name=BRANCH_ID_FIELD,
            label="Neon branch ID",
            prompt="Branch ID for storage tools (optional, defaults to the project's default branch)",
            env_var=NEON_BRANCH_ID_ENV,
            required=False,
        ),
    ),
    verify=verify_neon,
)

__all__ = [
    "API_KEY_FIELD",
    "BRANCH_ID_FIELD",
    "NEON_SETUP",
    "PROJECT_ID_FIELD",
]

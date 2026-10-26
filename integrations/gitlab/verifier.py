"""GitLab integration verifier."""

from __future__ import annotations

from integrations.gitlab.client import build_gitlab_config, validate_gitlab_config
from integrations.verification import register_validation_verifier

verify_gitlab = register_validation_verifier(
    "gitlab",
    build_config=build_gitlab_config,
    validate_config=validate_gitlab_config,
)

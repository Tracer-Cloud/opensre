"""GitLab integration package — the public API other tiers import.

The config/HTTP core lives in the ``client`` leaf; ``GITLAB_SETUP`` is
re-exported lazily so importing the package does not pull the setup/verifier
chain (and so the root does not import ``setup`` eagerly, keeping the graph
acyclic).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from integrations.gitlab.client import (
    DEFAULT_GITLAB_BASE_URL,
    GitlabConfig,
    GitlabValidationResult,
    build_gitlab_config,
    classify,
    get_gitlab_commits,
    get_gitlab_file,
    get_gitlab_mrs,
    get_gitlab_pipelines,
    gitlab_config_from_env,
    post_gitlab_mr_note,
    validate_gitlab_config,
    validate_gitlab_connection,
)


def __getattr__(name: str) -> object:
    """Lazily re-export ``GITLAB_SETUP`` from the setup submodule (PEP 562)."""
    if name == "GITLAB_SETUP":
        return importlib.import_module("integrations.gitlab.setup").GITLAB_SETUP
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:
    from integrations.gitlab.setup import GITLAB_SETUP


__all__ = [
    "DEFAULT_GITLAB_BASE_URL",
    "GITLAB_SETUP",
    "GitlabConfig",
    "GitlabValidationResult",
    "build_gitlab_config",
    "classify",
    "get_gitlab_commits",
    "get_gitlab_file",
    "get_gitlab_mrs",
    "get_gitlab_pipelines",
    "gitlab_config_from_env",
    "post_gitlab_mr_note",
    "validate_gitlab_config",
    "validate_gitlab_connection",
]

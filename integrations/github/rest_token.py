"""The GitHub token a GitHub REST tool runs with, and whether any resolves.

``analyze_github_ci_reliability`` receives the connected integration's token
through ``extract_params`` and falls back to the environment. The host's skill
prerequisite gate asks the same question before a GitHub demo starts. Both go
through this module, so the gate can never pass a demo the analyzer would then
refuse, or hold back one it would run.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.tool import availability_view
from integrations.github.client import resolve_github_token
from integrations.github.helpers import github_creds


def github_rest_token(
    sources: Mapping[str, Any] | None = None, *, explicit: str | None = None
) -> str:
    """Return ``explicit``, else the token of ``sources["github"]``, else the env token; ``""`` when none.

    ``sources`` is the tool-facing view (``availability_view``) the runtime
    builds ``extract_params`` from, so this is the token the tool is handed.
    """
    configured: str | None = None
    github = (sources or {}).get("github")
    if not explicit and isinstance(github, dict) and github:
        configured = github_creds(github).get("github_token")
    return resolve_github_token(explicit or configured)


def has_github_rest_token(resolved_integrations: Mapping[str, Any]) -> bool:
    """True when a GitHub REST tool run on ``resolved_integrations`` would find a token."""
    return bool(github_rest_token(availability_view(dict(resolved_integrations))))


__all__ = ["github_rest_token", "has_github_rest_token"]

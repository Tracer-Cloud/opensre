"""Git commit metadata constants."""

from __future__ import annotations

OPENSRE_COMMIT_COAUTHOR_NAME = "OpenSRE Agent"
OPENSRE_COMMIT_COAUTHOR_EMAIL = "opensreagent@opensre.com"
OPENSRE_COMMIT_COAUTHOR_TRAILER = (
    f"Co-authored-by: {OPENSRE_COMMIT_COAUTHOR_NAME} <{OPENSRE_COMMIT_COAUTHOR_EMAIL}>"
)
# Resolving a merge includes the agent's own test run; the general coding timeout is too short.
MERGE_RESOLUTION_TIMEOUT_SECONDS = 1800.0

__all__ = [
    "MERGE_RESOLUTION_TIMEOUT_SECONDS",
    "OPENSRE_COMMIT_COAUTHOR_EMAIL",
    "OPENSRE_COMMIT_COAUTHOR_NAME",
    "OPENSRE_COMMIT_COAUTHOR_TRAILER",
]

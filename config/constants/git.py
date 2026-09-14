"""Git commit metadata constants."""

from __future__ import annotations

OPENSRE_COMMIT_COAUTHOR_NAME = "OpenSRE Agent"
OPENSRE_COMMIT_COAUTHOR_EMAIL = "opensreagent@opensre.com"
OPENSRE_COMMIT_COAUTHOR_TRAILER = (
    f"Co-authored-by: {OPENSRE_COMMIT_COAUTHOR_NAME} <{OPENSRE_COMMIT_COAUTHOR_EMAIL}>"
)

__all__ = [
    "OPENSRE_COMMIT_COAUTHOR_EMAIL",
    "OPENSRE_COMMIT_COAUTHOR_NAME",
    "OPENSRE_COMMIT_COAUTHOR_TRAILER",
]

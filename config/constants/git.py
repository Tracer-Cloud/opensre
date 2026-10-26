"""Git constants: commit metadata and the environment of non-interactive git calls."""

from __future__ import annotations

OPENSRE_COMMIT_COAUTHOR_NAME = "OpenSRE Agent"
OPENSRE_COMMIT_COAUTHOR_EMAIL = "opensreagent@opensre.com"
OPENSRE_COMMIT_COAUTHOR_TRAILER = (
    f"Co-authored-by: {OPENSRE_COMMIT_COAUTHOR_NAME} <{OPENSRE_COMMIT_COAUTHOR_EMAIL}>"
)
# Resolving a merge includes the agent's own test run; the general coding timeout is too short.
MERGE_RESOLUTION_TIMEOUT_SECONDS = 1800.0
# Git's own variables: "0" stops it prompting for credentials on the terminal, and
# stops read-only commands such as ``git status`` from taking the index lock to
# rewrite ``.git/index``.
GIT_TERMINAL_PROMPT_ENV = "GIT_TERMINAL_PROMPT"
GIT_ALLOW_PROTOCOL_ENV = "GIT_ALLOW_PROTOCOL"
GIT_OPTIONAL_LOCKS_ENV = "GIT_OPTIONAL_LOCKS"

__all__ = [
    "GIT_ALLOW_PROTOCOL_ENV",
    "GIT_OPTIONAL_LOCKS_ENV",
    "GIT_TERMINAL_PROMPT_ENV",
    "MERGE_RESOLUTION_TIMEOUT_SECONDS",
    "OPENSRE_COMMIT_COAUTHOR_EMAIL",
    "OPENSRE_COMMIT_COAUTHOR_NAME",
    "OPENSRE_COMMIT_COAUTHOR_TRAILER",
]

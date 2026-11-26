"""Re-attach the saved GitHub handle to product analytics when the REPL starts."""

from __future__ import annotations

from infrastructure.analytics.github_identity import identify_github_username
from integrations.github import saved_github_username


def identify_saved_github_username() -> None:
    """Attach the GitHub handle saved by an earlier login to this process's analytics.

    The integration store keeps ``credentials.username`` across REPL sessions, but
    analytics persistent properties live per process. Calling this at REPL boot lets
    events like ``$ai_generation`` carry ``github_username`` without a fresh
    device-flow login.
    """
    identify_github_username(saved_github_username())

"""Product-level statements shown to users in more than one place.

Release maturity appears on the README badge, in the README callout, and in the
CLI banner. Held here so those cannot drift apart — they did, with the README
saying "Public Alpha" while the CLI said "Public Beta".
"""

from __future__ import annotations

from typing import Final

#: The CLI command identity (lowercase, matches the ``opensre`` executable).
PRODUCT_NAME: Final[str] = "opensre"

#: The brand name for display headers (launch banner, sign-in screen).
PRODUCT_DISPLAY_NAME: Final[str] = "OpenSRE"

#: Sign-in / welcome screen copy, shown when the shell requires login.
WELCOME_TITLE: Final[str] = "Welcome to OpenSRE CLI"
WELCOME_DESCRIPTION: Final[str] = (
    "OpenSRE is an AI-powered DevOps agent that diagnoses, fixes and "
    "optimizes your production software."
)
SIGN_IN_PROMPT: Final[str] = "Sign in or create an OpenSRE account to use the interactive shell."

#: Release maturity, as users see it. Keep in step with the README badge.
RELEASE_STAGE: Final[str] = "Public Alpha"

#: The one-line maturity banner printed before the landing page.
RELEASE_STAGE_BANNER: Final[str] = (
    f"🚧 OpenSRE is in {RELEASE_STAGE} — core workflows are usable, "
    "and APIs and integrations may still change."
)

#: Overrides the GitHub releases endpoint the update check reads (tests, mirrors).
RELEASES_API_URL_ENV: Final[str] = "OPENSRE_RELEASES_API_URL"

#: Set by ``uv run`` on child processes; its presence marks a development checkout.
UV_RUN_RECURSION_DEPTH_ENV: Final[str] = "UV_RUN_RECURSION_DEPTH"

#: Prevent a CLI wizard opened inside the REPL from launching a nested shell.
OPENSRE_PARENT_INTERACTIVE_SHELL_ENV: Final[str] = "OPENSRE_PARENT_INTERACTIVE_SHELL"

__all__ = [
    "OPENSRE_PARENT_INTERACTIVE_SHELL_ENV",
    "PRODUCT_DISPLAY_NAME",
    "PRODUCT_NAME",
    "RELEASES_API_URL_ENV",
    "RELEASE_STAGE",
    "RELEASE_STAGE_BANNER",
    "SIGN_IN_PROMPT",
    "UV_RUN_RECURSION_DEPTH_ENV",
    "WELCOME_DESCRIPTION",
    "WELCOME_TITLE",
]

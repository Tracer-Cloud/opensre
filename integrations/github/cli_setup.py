"""Configure GitHub integration via local GitHub CLI session or OpenSRE app handoff."""

from __future__ import annotations

import logging
import sys
import webbrowser
from typing import Any

from config.account import load_account_record
from integrations.github.app_connection import github_setup_url
from integrations.github.cli_probe import (
    GitHubCLIAuthStatus,
    GitHubCLIProbeResult,
    detect_github_cli,
)
from integrations.setup.guidance import TerminalSetupUI
from integrations.setup.result import SetupPending
from integrations.setup_flow import SetupUI
from integrations.store import upsert_integration

logger = logging.getLogger(__name__)

GH_INSTALL_DOCS_URL = "https://cli.github.com"


def _handoff_to_app(url: str) -> SetupPending:
    """Open or print the app setup page and leave setup pending."""
    try:
        opened = bool(webbrowser.open(url))
    except (webbrowser.Error, OSError):
        opened = False
    print(f"\n  {'Opened' if opened else 'Open'} {url}")
    print("  Connect GitHub there with browser sign-in or the app's token form. Setup is pending.")
    if load_account_record() is None:
        print("  Run `opensre account login` to load your app connections on this machine.")
    print("  After connecting, return to OpenSRE and refresh your connections.")
    return SetupPending(service="github", setup_url=url)


def _persist_cli_session(
    *,
    username: str,
    hostname: str,
) -> None:
    """Persist local GitHub CLI non-secret session metadata in the integration store.

    Strict security requirement: Never store, copy, or manage tokens.
    Preserves existing MCP GitHub records by storing the CLI preference under
    the distinct `github_cli` service.
    """
    entry: dict[str, Any] = {
        "service": "github_cli",
        "status": "active",
        "credentials": {
            "auth_mode": "github_cli",
            "hostname": hostname,
            "username": username,
        },
    }
    upsert_integration("github_cli", entry)


def setup_github(*, ui: SetupUI | None = None) -> str | SetupPending:
    """Guide GitHub integration setup.

    Probes the local GitHub CLI (`gh`) first:
    - If `gh` is installed and authenticated: offers to use the existing local CLI
      session or proceed with the OpenSRE app MCP setup.
    - If `gh` is installed but unauthenticated: offers to retry after `gh auth login`,
      or continue with the OpenSRE app setup.
    - If `gh` is missing: shows installation guidance, offers retry or continue
      with the OpenSRE app setup.

    When the user selects the CLI session, only non-secret metadata (auth_mode,
    hostname, username) is persisted. Never touches tokens.
    """
    active_ui = ui or TerminalSetupUI()
    url = github_setup_url()

    while True:
        probe: GitHubCLIProbeResult = detect_github_cli()

        if not probe.installed:
            active_ui.say("\nGitHub CLI (`gh`) was not detected on this machine.")
            active_ui.say(f"Install guidance: {GH_INSTALL_DOCS_URL}")
            active_ui.say("  - macOS: brew install gh")
            active_ui.say("  - Linux: sudo apt install gh (or package manager)")
            active_ui.say("  - Windows: winget install GitHub.cli")
            active_ui.say("You can also continue using the hosted OpenSRE app connection.\n")

            try:
                choice = active_ui.choose(
                    "How would you like to proceed?",
                    [
                        ("app", "Continue with OpenSRE app setup (browser / PAT)"),
                        ("retry", "Retry GitHub CLI detection"),
                        ("cancel", "Cancel"),
                    ],
                )
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                sys.exit(1)

            if choice == "retry":
                continue
            if choice == "app":
                return _handoff_to_app(url)
            print("\nAborted.")
            sys.exit(1)

        # probe.installed is True
        if probe.auth_status == GitHubCLIAuthStatus.AUTHENTICATED and probe.active_account:
            acc = probe.active_account
            active_ui.say(
                f"\nDetected GitHub CLI session: @{acc.username} on {acc.hostname}"
                + (f" (version {probe.version})" if probe.version else "")
            )

            try:
                choice = active_ui.choose(
                    "Select GitHub integration method:",
                    [
                        (
                            "cli",
                            f"Use existing GitHub CLI session (@{acc.username} on {acc.hostname})",
                        ),
                        ("app", "Set up hosted OpenSRE connection instead (app.opensre.com)"),
                        ("retry", "Re-probe GitHub CLI"),
                        ("cancel", "Cancel"),
                    ],
                )
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                sys.exit(1)

            if choice == "retry":
                continue
            if choice == "app":
                return _handoff_to_app(url)
            if choice == "cli":
                _persist_cli_session(username=acc.username, hostname=acc.hostname)
                active_ui.say(
                    f"Configured GitHub integration to use local GitHub CLI session (@{acc.username} on {acc.hostname})."
                )
                return "github_cli"
            print("\nAborted.")
            sys.exit(1)

        elif probe.auth_status == GitHubCLIAuthStatus.NOT_AUTHENTICATED:
            active_ui.say(
                "\nGitHub CLI is installed"
                + (f" (version {probe.version})" if probe.version else "")
                + ", but no active authenticated session was found."
            )
            active_ui.say("To log in with GitHub CLI, run in another terminal: `gh auth login`\n")

            try:
                choice = active_ui.choose(
                    "How would you like to proceed?",
                    [
                        ("app", "Continue with OpenSRE app setup (browser / PAT)"),
                        ("retry", "Retry check (after running `gh auth login`)"),
                        ("cancel", "Cancel"),
                    ],
                )
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                sys.exit(1)

            if choice == "retry":
                continue
            if choice == "app":
                return _handoff_to_app(url)
            print("\nAborted.")
            sys.exit(1)

        else:
            # UNKNOWN auth status / probe error
            active_ui.say(
                "\nGitHub CLI is installed"
                + (f" (version {probe.version})" if probe.version else "")
                + f", but authentication status could not be verified: {probe.detail or 'unknown error'}"
            )

            try:
                choice = active_ui.choose(
                    "How would you like to proceed?",
                    [
                        ("app", "Continue with OpenSRE app setup (browser / PAT)"),
                        ("retry", "Retry GitHub CLI check"),
                        ("cancel", "Cancel"),
                    ],
                )
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                sys.exit(1)

            if choice == "retry":
                continue
            if choice == "app":
                return _handoff_to_app(url)
            print("\nAborted.")
            sys.exit(1)


__all__ = ["setup_github"]

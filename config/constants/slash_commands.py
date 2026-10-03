"""Slash-command literals and result keys shared across surfaces and core offer/dispatch code."""

from __future__ import annotations

from typing import Final

#: Connect an integration. Offers build it and recognise it when dispatched.
INTEGRATIONS_SETUP_COMMAND: Final[str] = "/integrations setup"

#: Prefix form, with the trailing space that separates the service id.
INTEGRATIONS_SETUP_PREFIX: Final[str] = f"{INTEGRATIONS_SETUP_COMMAND} "

#: Tool-result key naming a command queued to run as the user's next turn.
QUEUED_COMMAND_KEY: Final[str] = "queued_command"

__all__ = [
    "INTEGRATIONS_SETUP_COMMAND",
    "INTEGRATIONS_SETUP_PREFIX",
    "QUEUED_COMMAND_KEY",
]

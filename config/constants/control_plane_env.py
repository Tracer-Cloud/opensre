"""Environment variables that hold this host's own platform credentials.

On a host serving an organization these authenticate the process to the
OpenSRE control plane: the account token that reads any member's connections,
the bearer tokens that open the prompt port, the billing and identity secrets,
the gateway's own database. A chat member can drive commands on that host, and
a command inherits its parent's environment unless told otherwise, so these
names are taken out of every such child.

This narrows what a command is handed. A child running as the same user can
still read its parent's start-up environment from the operating system, so the
names here do not replace keeping these secrets away from that user.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from config.constants.account import OPENSRE_ACCOUNT_TOKEN_ENV
from config.constants.billing import MACHINE_SECRET_ENV, USAGE_SECRET_ENV
from config.constants.gateway import DATABASE_URL_ENV
from config.constants.organization import organization_id

#: This organization's own bearer token for the gateway's prompt port.
GATEWAY_LISTENER_TOKEN_ENV: Final[str] = "OPENSRE_GATEWAY_LISTENER_TOKEN"
#: The fleet-wide token every gateway used to share; the local shell also sets it.
LEGACY_LISTENER_TOKEN_ENV: Final[str] = "OPENSRE_ALERT_LISTENER_TOKEN"

CONTROL_PLANE_SECRET_ENV_NAMES: Final[frozenset[str]] = frozenset(
    {
        OPENSRE_ACCOUNT_TOKEN_ENV,
        MACHINE_SECRET_ENV,
        USAGE_SECRET_ENV,
        DATABASE_URL_ENV,
        GATEWAY_LISTENER_TOKEN_ENV,
        LEGACY_LISTENER_TOKEN_ENV,
        "OPENSRE_PAIRING_SECRET",
        "SLACK_SIGNING_SECRET",
    }
)


def without_control_plane_secrets(environment: Mapping[str, str]) -> dict[str, str]:
    """``environment`` for a child process, minus this host's platform credentials.

    A process serving no organization is the user's own machine: its
    environment is theirs and passes through unchanged.
    """
    if not organization_id():
        return dict(environment)
    return {
        name: value
        for name, value in environment.items()
        if name not in CONTROL_PLANE_SECRET_ENV_NAMES
    }


__all__ = [
    "CONTROL_PLANE_SECRET_ENV_NAMES",
    "GATEWAY_LISTENER_TOKEN_ENV",
    "LEGACY_LISTENER_TOKEN_ENV",
    "without_control_plane_secrets",
]

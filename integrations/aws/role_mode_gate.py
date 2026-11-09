"""Decide how AWS setup proceeds when the role mode has no base credentials.

Assuming a role needs credentials boto3 can already find; the role mode
collects only the ARN. On a machine with none, letting the user type an ARN and
then failing STS is a dead end. This module holds the decision so the CLI and
the onboarding wizard steer identically; each supplies its own way of asking.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from integrations.aws.credential_chain import AMBIENT_SOURCES_HINT, has_ambient_credentials

ROLE_MODE = "role"
KEYS_MODE = "keys"


class RoleGateChoice(StrEnum):
    """What the user picked when the ambient credential chain was empty."""

    USE_KEYS = "keys"
    CONFIGURE_FIRST = "configure-first"
    CONTINUE_ROLE = "continue-role"


@dataclass(frozen=True)
class GateOption:
    value: RoleGateChoice
    label: str
    hint: str


NO_AMBIENT_CREDENTIALS_NOTICE = (
    "No base AWS credentials found on this machine. A role is assumed *with* base "
    f"credentials — {AMBIENT_SOURCES_HINT} — so this option cannot work here yet."
)

CONFIGURE_FIRST_INSTRUCTION = (
    "Configure base credentials in this shell — `aws configure`, or export "
    "AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY for an identity allowed sts:AssumeRole on "
    "the role — then run setup again and pick IAM Role ARN."
)

GATE_QUESTION = "How would you like to continue?"

GATE_OPTIONS: tuple[GateOption, ...] = (
    GateOption(
        RoleGateChoice.USE_KEYS,
        "Use an access key + secret instead",
        "simplest on a laptop; the key needs the read-only policies from the AWS docs",
    ),
    GateOption(
        RoleGateChoice.CONFIGURE_FIRST,
        "Configure base credentials first, then come back",
        "run `aws configure` or export AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY for an "
        "identity allowed sts:AssumeRole on the role, then re-run setup",
    ),
    GateOption(
        RoleGateChoice.CONTINUE_ROLE,
        "Continue with the role anyway",
        "only for OpenSRE running on EC2/ECS/Lambda with an attached role",
    ),
)

#: How a surface asks the gate question: shows the notice, offers GATE_OPTIONS,
#: returns the picked choice (or None when the user cancelled).
AskGate = Callable[[], RoleGateChoice | None]


class ConfigureCredentialsFirst(Exception):
    """The user chose to configure base credentials before setting up the role."""


def gate_role_mode(mode: str, *, ask: AskGate) -> str:
    """Return the mode to continue with after checking the role mode's prerequisite.

    Only the role mode is gated. When the ambient chain is empty, *ask* runs and
    the answer steers: keys mode, the role anyway, or
    :class:`ConfigureCredentialsFirst` so the surface can leave setup cleanly
    with the instruction to come back.
    """
    if mode != ROLE_MODE or has_ambient_credentials():
        return mode
    choice = ask()
    if choice is None or choice is RoleGateChoice.CONFIGURE_FIRST:
        raise ConfigureCredentialsFirst
    if choice is RoleGateChoice.USE_KEYS:
        return KEYS_MODE
    return ROLE_MODE


__all__ = [
    "CONFIGURE_FIRST_INSTRUCTION",
    "GATE_OPTIONS",
    "GATE_QUESTION",
    "KEYS_MODE",
    "NO_AMBIENT_CREDENTIALS_NOTICE",
    "ROLE_MODE",
    "AskGate",
    "ConfigureCredentialsFirst",
    "GateOption",
    "RoleGateChoice",
    "gate_role_mode",
]

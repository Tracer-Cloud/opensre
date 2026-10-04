"""What prompt assembly does differently on each surface.

One row per surface, so the differences are readable in one place rather than as
ternaries spread through the builders. A leaf module: it holds flags only, never
prompt text, so both the builders and the context provider can import it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PromptSurface(StrEnum):
    """A surface the harness assembles prompts for."""

    INTERACTIVE_SHELL = "interactive_shell"
    HEADLESS_CLI = "headless_cli"
    GATEWAY = "gateway"


@dataclass(frozen=True, slots=True)
class SurfaceProfile:
    """The complete set of prompt differences for one surface."""

    surface: PromptSurface
    #: Terminology, setup guidance and response-shape rules. Written for a
    #: terminal reader, so a chat surface replaces them with its own persona.
    cli_rules: bool
    #: Vendor-owned persona fragments join the prompt in place of the CLI rules.
    vendor_persona: bool
    #: The operator's connected integrations and schedules. Scoped to one
    #: installation, so a shared chat surface does not report it to every member.
    setup_state: bool
    #: This host's uptime, disk and memory beside the per-turn clock. They
    #: describe the machine this process runs on, so a shared chat surface does
    #: not report them to every member; it gets the clock alone.
    host_measurements: bool


_PROFILES: dict[PromptSurface, SurfaceProfile] = {
    PromptSurface.INTERACTIVE_SHELL: SurfaceProfile(
        surface=PromptSurface.INTERACTIVE_SHELL,
        cli_rules=True,
        vendor_persona=False,
        setup_state=True,
        host_measurements=True,
    ),
    PromptSurface.HEADLESS_CLI: SurfaceProfile(
        surface=PromptSurface.HEADLESS_CLI,
        cli_rules=True,
        vendor_persona=False,
        setup_state=True,
        host_measurements=True,
    ),
    PromptSurface.GATEWAY: SurfaceProfile(
        surface=PromptSurface.GATEWAY,
        cli_rules=False,
        vendor_persona=True,
        setup_state=False,
        host_measurements=False,
    ),
}


def profile_for(surface: str) -> SurfaceProfile:
    """Return the profile for ``surface``, defaulting to the interactive shell.

    An unknown surface reads as the shell rather than raising: a new caller gets
    the full prompt instead of a silently stripped one.
    """
    try:
        known = PromptSurface(surface)
    except ValueError:
        known = PromptSurface.INTERACTIVE_SHELL
    return _PROFILES[known]


def known_profile(surface: str | None) -> SurfaceProfile | None:
    """Return the profile for a recognised ``surface``, or ``None``.

    For facts about one installation, where guessing wrong discloses them:
    unlike :func:`profile_for`, a missing or unrecognised surface is not the shell.
    """
    if surface is None:
        return None
    try:
        return _PROFILES[PromptSurface(surface)]
    except ValueError:
        return None


__all__ = ["PromptSurface", "SurfaceProfile", "known_profile", "profile_for"]

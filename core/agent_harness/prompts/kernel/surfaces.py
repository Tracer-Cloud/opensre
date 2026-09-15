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
    #: The memory store is host-global, so a shared chat surface only injects it
    #: when a deployment opts in.
    long_term_memory_by_default: bool
    #: The operator's connected integrations and schedules. Scoped to one
    #: installation, so a shared chat surface does not report it to every member.
    setup_state: bool


_PROFILES: dict[PromptSurface, SurfaceProfile] = {
    PromptSurface.INTERACTIVE_SHELL: SurfaceProfile(
        surface=PromptSurface.INTERACTIVE_SHELL,
        cli_rules=True,
        vendor_persona=False,
        long_term_memory_by_default=True,
        setup_state=True,
    ),
    PromptSurface.HEADLESS_CLI: SurfaceProfile(
        surface=PromptSurface.HEADLESS_CLI,
        cli_rules=True,
        vendor_persona=False,
        long_term_memory_by_default=True,
        setup_state=True,
    ),
    PromptSurface.GATEWAY: SurfaceProfile(
        surface=PromptSurface.GATEWAY,
        cli_rules=False,
        vendor_persona=True,
        long_term_memory_by_default=False,
        setup_state=False,
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


__all__ = ["PromptSurface", "SurfaceProfile", "profile_for"]

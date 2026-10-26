"""Process-wide CLI runtime flags (json, verbose, yes, onboarding, interactive).

Lives in ``infrastructure.process`` so integrations and tools can read the same flag
contract without importing the CLI package. The CLI root callback populates
these via :func:`cli.runtime_flags.sync_runtime_flags_from_click`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RuntimeFlags:
    json: bool = False
    verbose: bool = False
    debug: bool = False
    yes: bool = False
    onboarding: bool = True
    interactive: bool = True


_flags = RuntimeFlags()


def configure_runtime_flags(
    *,
    json: bool | None = None,
    verbose: bool | None = None,
    debug: bool | None = None,
    yes: bool | None = None,
    onboarding: bool | None = None,
    interactive: bool | None = None,
) -> None:
    """Replace one or more runtime flags (used by the CLI click bridge)."""
    global _flags
    updates: dict[str, bool] = {}
    if json is not None:
        updates["json"] = json
    if verbose is not None:
        updates["verbose"] = verbose
    if debug is not None:
        updates["debug"] = debug
    if yes is not None:
        updates["yes"] = yes
    if onboarding is not None:
        updates["onboarding"] = onboarding
    if interactive is not None:
        updates["interactive"] = interactive
    if updates:
        _flags = RuntimeFlags(**{**_flags.__dict__, **updates})


def reset_runtime_flags() -> None:
    global _flags
    _flags = RuntimeFlags()


def is_json_output() -> bool:
    """True when the user passed ``--json`` / ``-j``."""
    return _flags.json


def is_verbose() -> bool:
    """True when the user passed ``--verbose``."""
    return _flags.verbose


def is_debug() -> bool:
    """True when the user passed ``--debug``."""
    return _flags.debug


def is_yes() -> bool:
    """True when the user passed ``--yes`` / ``-y``."""
    return _flags.yes


def is_onboarding_enabled() -> bool:
    """True unless the user passed ``--skip-onboarding``."""
    return _flags.onboarding

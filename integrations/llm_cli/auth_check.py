"""Integrations-backed CLI auth checker that fills the config port.

Maps a registered CLI adapter's live ``detect()`` result onto the config-tier
:class:`config.llm_auth.cli_auth.CliAuthState`, so config can report CLI auth
status without importing this package.
"""

from __future__ import annotations

from config.llm_auth.cli_auth import CliAuthState


def check_cli_auth(provider: str) -> CliAuthState | None:
    """Check a CLI provider's local install and login via its registered adapter."""
    from integrations.llm_cli.registry import get_cli_provider_registration

    reg = get_cli_provider_registration(provider)
    if reg is None:
        return None
    result = reg.adapter_factory().detect()
    return CliAuthState(
        installed=result.installed,
        logged_in=result.logged_in,
        detail=result.detail,
    )


__all__ = ["check_cli_auth"]

"""Setup results that do not represent verified, persisted credentials."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SetupPending:
    """An external setup handoff awaiting a refreshed connection."""

    service: str
    setup_url: str

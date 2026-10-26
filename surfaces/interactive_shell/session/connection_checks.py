"""Session-local display results from explicitly requested connection checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from config.principal import StorageScope

type ConnectionCheckKey = tuple[StorageScope | None, tuple[int, int, int]]


@dataclass(frozen=True)
class ConnectionCheck:
    """A last-known display result, not a live health guarantee or credential cache."""

    source: str
    status: str
    detail: str
    checked_at: datetime

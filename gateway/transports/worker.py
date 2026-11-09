"""Background worker that owns one transport's connection."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class TransportWorker(Protocol):
    def stop(self, *, timeout: float = ...) -> bool:
        """Stop the worker and return whether it shut down within ``timeout``."""


# Each transport's startup returns its worker plus its own settings object.
TransportStarter = Callable[..., tuple[TransportWorker, Any]]

"""Container start: install the current main-channel binary, then run MODE."""

from infrastructure.deployment.container.entrypoint import main, supervise

__all__ = ["main", "supervise"]

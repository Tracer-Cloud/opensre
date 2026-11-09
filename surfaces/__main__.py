"""Make the whole product executable: ``python -m surfaces``.

Runs :mod:`surfaces.entrypoint`, the same entry as the ``opensre`` console
script and the frozen binary — CLI, interactive shell and gateway composed.
``python -m surfaces.cli`` runs the command surface alone, without a shell.
"""

from __future__ import annotations

from surfaces.entrypoint import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())

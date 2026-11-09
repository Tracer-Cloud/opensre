"""Make the package executable: ``python -m surfaces.cli``.

The CLI itself is defined in :mod:`surfaces.cli.app`. This module launches the
command surface **alone**: no interactive shell to fall back to and no
foreground gateway runner, because those come from the process entrypoint. For
the whole product use ``opensre`` or ``python -m surfaces``.
"""

from __future__ import annotations

from surfaces.cli.app import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())

"""Package guard for the OpenSRE messaging gateway.

This module is intentionally **not** a production composition root: slash-command
ports are injected by the CLI composition root outside this package. Starting here
would boot chat without ``slash_invoke``.

Production: ``opensre gateway start`` / ``opensre gateway start --foreground``.
"""

from __future__ import annotations

_BARE_MAIN_EXIT = (
    "python -m gateway has no slash-port glue and is not a production entry.\n"
    "Start the gateway with: opensre gateway start\n"
    "                  or: opensre gateway start --foreground\n"
)


def main() -> None:
    """Refuse bare package main — production must use the CLI composition root."""
    raise SystemExit(_BARE_MAIN_EXIT)


__all__ = ["main"]


if __name__ == "__main__":
    main()

"""CLI entry point — delegates to app.cli."""

from __future__ import annotations


def main() -> None:
    from app.cli.__main__ import cli

    cli()


if __name__ == "__main__":
    main()

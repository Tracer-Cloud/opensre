"""Makefile invokes ``python -m tests.chaos_engineering``; you normally use ``make chaos-*`` instead."""

from __future__ import annotations

from tests.chaos_engineering.cli import cli

if __name__ == "__main__":
    cli.main(prog_name="python -m tests.chaos_engineering")

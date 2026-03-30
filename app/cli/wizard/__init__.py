"""Quickstart wizard entrypoints."""

from __future__ import annotations


def run_wizard(*args, **kwargs):
    """Import the wizard flow lazily to keep package import side effects small."""
    from app.cli.wizard.flow import run_wizard as _run_wizard

    return _run_wizard(*args, **kwargs)

__all__ = ["run_wizard"]

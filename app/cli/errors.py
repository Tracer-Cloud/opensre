"""Structured CLI error with optional suggestion and docs URL.

Follows the pattern from `clig.dev <https://clig.dev/>`_ and flyctl's
error system: every user-facing error can carry a human-readable
suggestion (what to do next) and a docs link.
"""

from __future__ import annotations

import sys
import typing as t

import click


class OpenSREError(click.ClickException):
    """A CLI error that renders with an optional suggestion and docs URL."""

    def __init__(
        self,
        message: str,
        *,
        suggestion: str | None = None,
        docs_url: str | None = None,
        exit_code: int = 1,
    ) -> None:
        super().__init__(message)
        self.suggestion = suggestion
        self.docs_url = docs_url
        self.exit_code = exit_code

    def format_message(self) -> str:
        parts = [self.message]
        if self.suggestion:
            parts.append(f"\nSuggestion: {self.suggestion}")
        if self.docs_url:
            parts.append(f"Docs: {self.docs_url}")
        return "\n".join(parts)

    def show(self, file: t.IO[t.Any] | None = None) -> None:
        if file is None:
            file = sys.stderr
        click.echo(click.style("Error: ", fg="red", bold=True) + self.format_message(), file=file)

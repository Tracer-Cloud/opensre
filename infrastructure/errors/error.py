"""Structured, frontend-agnostic error for opensre.

Carries a human-readable suggestion (what to do next) and an optional docs
link, following the `clig.dev <https://clig.dev/>`_ / flyctl convention. Lives
in ``infrastructure.errors`` so any layer (CLI, tools, integrations, infra) can raise
and catch the same error contract without importing the CLI package.

Rendering is a CLI concern: ``surfaces.shared.error_handling.errors``
defines a ``click.ClickException`` subclass so CLI-raised errors render through
Click's existing path, and ``cli.app`` renders base errors raised by
non-CLI code. Catch this base type to handle both.
"""

from __future__ import annotations


class OpenSREError(Exception):
    """A structured error carrying an optional suggestion and docs URL."""

    def __init__(
        self,
        message: str,
        *,
        suggestion: str | None = None,
        docs_url: str | None = None,
        exit_code: int = 1,
    ) -> None:
        super().__init__(message)
        self.message = message
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

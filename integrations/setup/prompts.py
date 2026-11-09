"""Interactive credential prompts shared by integration setup commands."""

from __future__ import annotations

import sys
from typing import Any, NoReturn

import questionary

from infrastructure.terminal.prompt_support import QUESTIONARY_QMARK, questionary_prompt_style


def select(message: str, choices: list[Any], **kwargs: Any) -> Any:
    return questionary.select(
        message,
        choices=choices,
        qmark=QUESTIONARY_QMARK,
        style=questionary_prompt_style(),
        **kwargs,
    ).ask()


def confirm(message: str, **kwargs: Any) -> Any:
    return questionary.confirm(
        message, qmark=QUESTIONARY_QMARK, style=questionary_prompt_style(), **kwargs
    ).ask()


def prompt_value(label: str, default: str = "", secret: bool = False) -> str:
    try:
        if secret:
            result = questionary.password(
                label, qmark=QUESTIONARY_QMARK, style=questionary_prompt_style()
            ).ask()
        else:
            result = questionary.text(
                label,
                default=default,
                qmark=QUESTIONARY_QMARK,
                style=questionary_prompt_style(),
            ).ask()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(1)
    if result is None:
        print("\nAborted.")
        sys.exit(1)
    return result.strip() or default


def die(message: str) -> NoReturn:
    print(f"  error: {message}", file=sys.stderr)
    sys.exit(1)

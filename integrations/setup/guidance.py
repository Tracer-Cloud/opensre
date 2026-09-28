"""Terminal hand-offs used by integration-specific setup guides."""

from __future__ import annotations

from collections.abc import Sequence

import questionary

from infrastructure.terminal.prompt_support import QUESTIONARY_QMARK, questionary_prompt_style


class TerminalSetupUI:
    """Collect human input without exposing credentials in output or prompt defaults."""

    def say(self, message: str) -> None:
        """Print plain text, including literal configuration links."""
        print(f"  {message}")

    def step(self, number: str, title: str) -> None:
        """Announce one of the three bounded setup stages."""
        self.say(f"\nStep {number} of three — {title}")

    def choose(self, message: str, choices: Sequence[tuple[str, str]]) -> str:
        """Return a selected value, or interrupt setup when the user cancels."""
        answer = questionary.select(
            message,
            choices=[questionary.Choice(label, value=value) for value, label in choices],
            qmark=QUESTIONARY_QMARK,
            style=questionary_prompt_style(),
        ).ask()
        if answer is None or answer == "cancel":
            raise KeyboardInterrupt
        return str(answer)

    def value(self, message: str, *, default: str = "", secret: bool = False) -> str:
        """Ask for a required value, retaining a secret only on an explicit keep choice."""
        if secret and default:
            if (
                self.choose(message, [("keep", "Use the saved credential"), ("edit", "Replace it")])
                == "keep"
            ):
                return default
            default = ""
        while True:
            prompt = questionary.password if secret else questionary.text
            answer = prompt(
                message,
                default=default,
                qmark=QUESTIONARY_QMARK,
                style=questionary_prompt_style(),
            ).ask()
            if answer is None:
                raise KeyboardInterrupt
            value = str(answer).strip() or default
            if value:
                return value
            self.say("Enter a value, or press Ctrl+C to cancel.")

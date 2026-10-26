"""Why an Ask User menu opened, as ``ask_user_prompt_rendered`` records it."""

from __future__ import annotations

from enum import StrEnum


class AskUserReason(StrEnum):
    """The declared reason a selection menu was put in front of the user.

    A menu without a declared reason records none; the reason is never
    inferred from the question text.
    """

    CHOICE = "choice"
    """The model asked the user to choose, through ``ask_user_choice``."""

    ENTRY_MENU = "entry_menu"
    """A skill's catalog entry menu, opened by the host when the skill is entered."""


__all__ = ["AskUserReason"]

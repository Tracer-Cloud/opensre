"""Human hand-off types a host renders: the ask-user question and answer block.

A surface renders :class:`AskUserQuestion` items as an interactive menu and
round-trips the user's selections through :func:`format_ask_user_answers` /
:func:`parse_ask_user_answers`. The hand-off marker itself lives in
:mod:`core.agent_harness.spi.prompt_chrome`.
"""

from __future__ import annotations

from core.agent_harness.session.pending_choice import (
    AskUserQuestion,
    format_ask_user_answers,
    parse_ask_user_answers,
    question_key,
)

__all__ = [
    "AskUserQuestion",
    "format_ask_user_answers",
    "parse_ask_user_answers",
    "question_key",
]

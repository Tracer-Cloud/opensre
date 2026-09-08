"""Structured questions awaiting the user's menu selection.

The ``ask_user_choice`` action tool writes a :class:`PendingUserChoice` onto the
session and queues the ``/choose`` slash command; the shell's ``/choose`` handler
pops the object and renders it as an inline arrow-key menu with exclusive stdin.

A single decision uses ``title`` + ``options``. Several blockers go in
``questions`` as one payload (batched Ask User) — not one question per turn.
Selected labels are auto-submitted as the next user message, so the agent
receives the decision as structured conversation input — no prose scraping
and no "Reply with 1, 2, or 3" free-text parsing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_ANSWER_HEADER = re.compile(r"^(\d+)\.\s+(.+)$")


@dataclass(frozen=True, slots=True)
class AskUserQuestion:
    """One question in a batched Ask User payload."""

    label: str
    """Short breadcrumb name (e.g. ``Codebase``, ``Metrics``)."""

    title: str
    """Full question shown for this step."""

    options: tuple[str, ...]
    """Option labels in display order."""

    multi_select: bool = False
    """When True, the menu shows checkboxes and the user may pick several options."""


@dataclass(frozen=True, slots=True)
class PendingUserChoice:
    """A blocking decision the user makes via the shell selection menu."""

    title: str
    """Wizard header, or the question when ``questions`` is empty."""

    options: tuple[str, ...]
    """Option labels for the single-question path."""

    questions: tuple[AskUserQuestion, ...] = ()
    """Batched Ask User questions; empty means a single ``title`` + ``options`` menu."""

    multi_select: bool = False
    """Multi-select for the single-question path (ignored when ``questions`` is set)."""

    commands: dict[str, str] = field(default_factory=dict)
    """Option label -> slash command the shell runs instead of answering the model."""

    def items(self) -> tuple[AskUserQuestion, ...]:
        """Questions to render: ``questions`` when set, otherwise one from title/options."""
        if self.questions:
            return self.questions
        return (
            AskUserQuestion(
                label="",
                title=self.title,
                options=self.options,
                multi_select=self.multi_select,
            ),
        )

    def is_batch(self) -> bool:
        """True when the menu is a multi-question Ask User wizard."""
        return len(self.items()) >= 2


def format_ask_user_answers(
    questions: tuple[AskUserQuestion, ...],
    answers: tuple[str, ...],
) -> str:
    """Serialize Q→A pairs as the next user message after Ask User."""
    if len(questions) != len(answers):
        raise ValueError("questions and answers must be the same length")
    blocks: list[str] = []
    for index, (question, answer) in enumerate(zip(questions, answers, strict=True), start=1):
        blocks.append(f"{index}. {question.title}\n{answer}")
    return "\n\n".join(blocks)


def parse_ask_user_answers(text: str) -> list[tuple[str, str]]:
    """Parse :func:`format_ask_user_answers` output into ``(question, answer)`` pairs."""
    stripped = text.strip()
    if not stripped:
        return []
    pairs: list[tuple[str, str]] = []
    for block in stripped.split("\n\n"):
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            return []
        match = _ANSWER_HEADER.match(lines[0])
        if match is None:
            return []
        question = match.group(2).strip()
        answer = "\n".join(lines[1:]).strip()
        if not question or not answer:
            return []
        pairs.append((question, answer))
    return pairs


__all__ = [
    "AskUserQuestion",
    "PendingUserChoice",
    "format_ask_user_answers",
    "parse_ask_user_answers",
]

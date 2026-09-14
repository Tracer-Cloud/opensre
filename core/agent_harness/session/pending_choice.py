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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

_ANSWER_HEADER = re.compile(r"^(\d+)\.\s+(.+)$")
PENDING_USER_CHOICE_STATE_CUSTOM_TYPE = "pending_user_choice_state"


def question_key(title: str) -> str:
    """Identity of a question for matching it to an answer: whitespace and case folded."""
    return " ".join(title.split()).casefold()


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

    note: str = ""
    """Short explainer painted with the single-question menu; cleared when it closes."""

    commands: dict[str, str] = field(default_factory=dict)
    """Option label -> slash command the shell runs instead of answering the model."""

    custom_answer: bool = True
    """Offer the free-text row under the options (single-question path)."""

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


def pending_user_choice_state_snapshot(session: Any) -> dict[str, Any] | None:
    """Return the pending choice and its workflow context for persistence."""
    pending = getattr(session, "pending_user_choice", None)
    if not isinstance(pending, PendingUserChoice):
        return None
    return {
        "title": pending.title,
        "options": list(pending.options),
        "questions": [
            {
                "label": question.label,
                "title": question.title,
                "options": list(question.options),
                "multi_select": question.multi_select,
            }
            for question in pending.questions
        ],
        "multi_select": pending.multi_select,
        "note": pending.note,
        "commands": dict(pending.commands),
        "custom_answer": pending.custom_answer,
        "active_skill": getattr(session, "active_skill", None),
        "ask_user_rounds": int(getattr(session, "ask_user_rounds", 0)),
    }


def _last_pending_choice_content(
    prior_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    for record in reversed(prior_records):
        if record.get("type") != "custom_message":
            continue
        if record.get("custom_type") != PENDING_USER_CHOICE_STATE_CUSTOM_TYPE:
            continue
        content = record.get("content")
        return content if isinstance(content, dict) else None
    return None


def should_persist_pending_user_choice_state(
    snapshot: dict[str, Any] | None,
    *,
    prior_records: Sequence[Mapping[str, Any]],
) -> bool:
    """Return whether a changed pending-choice snapshot needs appending."""
    last = _last_pending_choice_content(prior_records)
    if snapshot is None:
        return last is not None
    return last != snapshot


def apply_pending_user_choice_state(session: Any, payload: Any) -> None:
    """Restore a pending choice and the active workflow that owns it."""
    if not hasattr(session, "pending_user_choice"):
        return
    if not isinstance(payload, dict) or not payload:
        session.pending_user_choice = None
        if hasattr(session, "active_skill"):
            session.active_skill = None
        return
    raw_questions = payload.get("questions")
    questions: list[AskUserQuestion] = []
    if isinstance(raw_questions, list):
        for item in raw_questions:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            raw_options = item.get("options")
            if not isinstance(title, str) or not isinstance(raw_options, list):
                continue
            questions.append(
                AskUserQuestion(
                    label=str(item.get("label") or ""),
                    title=title,
                    options=tuple(str(option) for option in raw_options),
                    multi_select=bool(item.get("multi_select", False)),
                )
            )
    raw_options = payload.get("options")
    options = tuple(str(option) for option in raw_options) if isinstance(raw_options, list) else ()
    raw_commands = payload.get("commands")
    commands = (
        {str(key): str(value) for key, value in raw_commands.items()}
        if isinstance(raw_commands, dict)
        else {}
    )
    session.pending_user_choice = PendingUserChoice(
        title=str(payload.get("title") or "Ask User"),
        options=options,
        questions=tuple(questions),
        multi_select=bool(payload.get("multi_select", False)),
        note=str(payload.get("note") or ""),
        commands=commands,
        custom_answer=bool(payload.get("custom_answer", True)),
    )
    if hasattr(session, "active_skill"):
        active_skill = payload.get("active_skill")
        session.active_skill = (
            active_skill if isinstance(active_skill, str) and active_skill else None
        )
    if hasattr(session, "ask_user_rounds"):
        rounds = payload.get("ask_user_rounds")
        session.ask_user_rounds = rounds if isinstance(rounds, int) and rounds >= 0 else 0


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
    "PENDING_USER_CHOICE_STATE_CUSTOM_TYPE",
    "PendingUserChoice",
    "apply_pending_user_choice_state",
    "format_ask_user_answers",
    "parse_ask_user_answers",
    "pending_user_choice_state_snapshot",
    "question_key",
    "should_persist_pending_user_choice_state",
]

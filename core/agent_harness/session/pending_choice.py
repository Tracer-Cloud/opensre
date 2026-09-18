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

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

PENDING_USER_CHOICE_STATE_CUSTOM_TYPE = "pending_user_choice_state"
_ANSWER_HEADER = re.compile(r"^(\d+)\.\s+(.+)\n", re.MULTILINE)
_LEGACY_ANSWER_HEADER = re.compile(r"^(\d+)\.\s+(.+)$", re.MULTILINE)
_ANSWER_JSON_PREFIX = "@json:"


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

    interaction_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """Opaque identifier joining prompt exposure to its eventual outcome."""

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
    skill_question_keys = getattr(session, "skill_question_keys", {})
    workflow_context = {
        "active_skill": getattr(session, "active_skill", None),
        "ask_user_rounds": int(getattr(session, "ask_user_rounds", 0)),
        "questions_already_answered": sorted(
            str(key) for key in getattr(session, "questions_already_answered", set())
        ),
        "skill_question_keys": (
            {
                skill: sorted(str(key) for key in keys)
                for skill, keys in skill_question_keys.items()
                if isinstance(skill, str) and skill and isinstance(keys, set)
            }
            if isinstance(skill_question_keys, dict)
            else {}
        ),
    }
    if not isinstance(pending, PendingUserChoice):
        return workflow_context if any(workflow_context.values()) else None
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
        "interaction_id": pending.interaction_id,
        **workflow_context,
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
        return bool(last)
    return last != snapshot


def apply_pending_user_choice_state(session: Any, payload: Any) -> None:
    """Restore a pending choice and the active workflow that owns it."""
    if not hasattr(session, "pending_user_choice"):
        return
    if not isinstance(payload, dict) or not payload:
        session.pending_user_choice = None
        _apply_workflow_context(session, {})
        return
    if "title" not in payload:
        session.pending_user_choice = None
        _apply_workflow_context(session, payload)
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
        interaction_id=str(payload.get("interaction_id") or uuid.uuid4()),
    )
    _apply_workflow_context(session, payload)


def _apply_workflow_context(session: Any, payload: Mapping[str, Any]) -> None:
    """Restore the state that survives after a pending choice is consumed."""
    if hasattr(session, "active_skill"):
        active_skill = payload.get("active_skill")
        session.active_skill = (
            active_skill if isinstance(active_skill, str) and active_skill else None
        )
    if hasattr(session, "ask_user_rounds"):
        rounds = payload.get("ask_user_rounds")
        session.ask_user_rounds = rounds if isinstance(rounds, int) and rounds >= 0 else 0
    answered = getattr(session, "questions_already_answered", None)
    if isinstance(answered, set):
        answered.clear()
        settled = payload.get("questions_already_answered")
        if isinstance(settled, list):
            answered.update(str(key) for key in settled if str(key))
    by_skill = getattr(session, "skill_question_keys", None)
    if isinstance(by_skill, dict):
        by_skill.clear()
        saved = payload.get("skill_question_keys")
        if isinstance(saved, dict):
            for skill, keys in saved.items():
                if not isinstance(skill, str) or not skill or not isinstance(keys, list):
                    continue
                restored = {str(key) for key in keys if str(key)}
                if restored:
                    by_skill[skill] = restored


def format_ask_user_answers(
    questions: tuple[AskUserQuestion, ...],
    answers: tuple[str, ...],
) -> str:
    """Serialize Q→A pairs as the next user message after Ask User."""
    if len(questions) != len(answers):
        raise ValueError("questions and answers must be the same length")
    blocks: list[str] = []
    for index, (question, answer) in enumerate(zip(questions, answers, strict=True), start=1):
        # JSON keeps every answer on one physical line, so blank lines and
        # numbered paragraphs in a custom answer cannot impersonate a later
        # question header when the next turn parses this hand-off payload.
        blocks.append(
            f"{index}. {question.title}\n{_ANSWER_JSON_PREFIX}"
            + json.dumps(answer, ensure_ascii=False)
        )
    return "\n\n".join(blocks)


def parse_ask_user_answers(text: str) -> list[tuple[str, str]]:
    """Parse :func:`format_ask_user_answers` output into ``(question, answer)`` pairs."""
    stripped = text.strip()
    if not stripped:
        return []

    framed = _parse_json_answer_blocks(stripped)
    if framed is not None:
        return framed
    return _parse_legacy_answer_blocks(stripped)


def _parse_json_answer_blocks(text: str) -> list[tuple[str, str]] | None:
    """Parse the unambiguous current answer framing, or signal legacy input."""
    cursor = 0
    pairs: list[tuple[str, str]] = []
    for index in range(1, len(text) + 1):
        header = _ANSWER_HEADER.match(text, cursor)
        if header is None:
            return [] if pairs else None
        if int(header.group(1)) != index:
            return []
        answer_start = header.end()
        if not text.startswith(_ANSWER_JSON_PREFIX, answer_start):
            return None if not pairs else []
        try:
            answer, used = json.JSONDecoder().raw_decode(
                text[answer_start + len(_ANSWER_JSON_PREFIX) :]
            )
        except json.JSONDecodeError:
            return []
        if not isinstance(answer, str) or not answer:
            return []
        cursor = answer_start + len(_ANSWER_JSON_PREFIX) + used
        pairs.append((header.group(2).strip(), answer))
        if cursor == len(text):
            return pairs
        if not text.startswith("\n\n", cursor):
            return []
        cursor += 2
    return []


def _parse_legacy_answer_blocks(text: str) -> list[tuple[str, str]]:
    """Read historical unframed answer messages persisted before JSON framing."""
    pairs: list[tuple[str, str]] = []
    cursor = 0
    for index in range(1, len(text) + 1):
        header = _LEGACY_ANSWER_HEADER.match(text, cursor)
        if header is None:
            return []
        if int(header.group(1)) != index:
            return []
        question = header.group(2).strip()
        answer_start = header.end()
        next_header = re.search(
            rf"\n\n{index + 1}\.\s+[^\n]+\n",
            text[answer_start:],
        )
        answer_end = answer_start + next_header.start() if next_header is not None else len(text)
        answer = text[answer_start:answer_end].strip()
        if not question or not answer:
            return []
        pairs.append((question, answer))
        if next_header is None:
            return pairs
        cursor = answer_start + next_header.start() + 2
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

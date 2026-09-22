"""Persistence and resume handling for the headless ask surface."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager

from core.agent_harness import SessionCore
from core.agent_harness.spi.defaults import default_session_repo
from core.agent_harness.spi.handoff import (
    AskUserQuestion,
    format_ask_user_answers,
    question_key,
)
from infrastructure.errors import OpenSREError
from infrastructure.turn_host.session_lock import (
    SessionExecutionBusyError,
    session_execution_lock,
)

_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def resolve_resume_session_id(reference: str) -> str:
    """Resolve an unambiguous persisted session reference to its full ID."""
    if _SAFE_SESSION_ID.fullmatch(reference) is None:
        raise OpenSREError(
            f"Session reference {reference!r} is invalid.",
            suggestion="Pass only the session ID or an unambiguous prefix.",
        )
    repo = default_session_repo()
    matches = repo.count_prefix_matches(reference)
    if matches == 0:
        raise OpenSREError(
            f"Session {reference!r} was not found.",
            suggestion="Use the session ID printed by an earlier `opensre ask` invocation.",
        )
    if matches > 1:
        raise OpenSREError(
            f"Session reference {reference!r} is ambiguous.",
            suggestion="Pass more characters from the session ID.",
        )
    loaded = repo.load_session(reference)
    if not isinstance(loaded, dict) or not loaded.get("session_id"):
        raise OpenSREError(f"Session {reference!r} could not be loaded.")
    session_id = str(loaded["session_id"])
    if _SAFE_SESSION_ID.fullmatch(session_id) is None:
        raise OpenSREError(f"Session {reference!r} has an invalid stored ID.")
    return session_id


@contextmanager
def ask_session_lock(session_id: str | None) -> Iterator[None]:
    """Reject overlapping processes that resume the same conversation."""
    if session_id is None:
        yield
        return
    try:
        with session_execution_lock(session_id, timeout=0):
            yield
    except SessionExecutionBusyError as exc:
        raise OpenSREError(
            f"Session {session_id!r} is busy in another process.",
            suggestion="Wait for that `opensre ask` invocation to finish, then retry.",
        ) from exc


def _selected_answer(
    question: AskUserQuestion,
    value: object,
    *,
    allow_custom: bool = True,
) -> str:
    """Normalize numeric headless selections while preserving free-text answers."""
    if isinstance(value, list):
        raw = [str(item).strip() for item in value if str(item).strip()]
    else:
        text = str(value).strip()
        raw = [part.strip() for part in text.split(",")] if question.multi_select else [text]
    answers: list[str] = []
    for item in raw:
        if item.isdigit() and 1 <= int(item) <= len(question.options):
            answers.append(question.options[int(item) - 1])
        elif item and (allow_custom or item in question.options):
            answers.append(item)
        elif item:
            raise OpenSREError(f"Choose one of the numbered options for {question.title!r}.")
    if not answers:
        raise OpenSREError(f"An answer is required for {question.title!r}.")
    if not question.multi_select and len(answers) != 1:
        raise OpenSREError(f"Choose one answer for {question.title!r}.")
    return ", ".join(answers)


def resume_prompt(session: SessionCore, prompt: str) -> str:
    """Turn the resumed CLI argument into the pending choice's Q→A payload."""
    pending = session.pending_user_choice
    if pending is None:
        session.ask_user_rounds = 0
        return prompt
    if pending.commands:
        raise OpenSREError(
            "This choice runs an interactive-shell command and cannot be answered headlessly.",
            suggestion=f"Run `opensre`, then `/resume {session.session_id}` and use `/choose`.",
        )
    questions = pending.items()
    answers: tuple[str, ...]
    if len(questions) == 1:
        answers = (
            _selected_answer(
                questions[0],
                prompt,
                allow_custom=pending.custom_answer,
            ),
        )
    else:
        try:
            supplied = json.loads(prompt)
        except json.JSONDecodeError as exc:
            raise OpenSREError(
                "This session is waiting for answers to multiple questions.",
                suggestion='Pass a JSON object such as \'{"Scope":"1","Window":"2"}\'.',
            ) from exc
        if not isinstance(supplied, dict):
            raise OpenSREError("Answers for multiple questions must be a JSON object.")
        values: list[str] = []
        for index, question in enumerate(questions, start=1):
            candidates = (question.label, question.title, str(index))
            value = next((supplied[key] for key in candidates if key in supplied), None)
            if value is None:
                raise OpenSREError(f"Missing answer for {question.title!r}.")
            values.append(_selected_answer(question, value))
        answers = tuple(values)
    session.pending_user_choice = None
    settled = getattr(session, "questions_already_answered", None)
    if isinstance(settled, set):
        settled.update(question_key(question.title) for question in questions)
    return format_ask_user_answers(questions, answers)


__all__ = ["ask_session_lock", "resolve_resume_session_id", "resume_prompt"]

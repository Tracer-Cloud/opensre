"""Sessions for turns nobody is watching: a remote prompt with no person on the other end.

A question or an approval ends the turn as a pending choice; the caller answers
later, and the same session resumes with that answer as its next message.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from core.agent_harness import SessionCore, SessionManager
from core.agent_harness.spi.handoff import AskUserQuestion, format_ask_user_answers, question_key
from core.agent_harness.spi.session_state import PendingUserChoice

APPROVE_OPTION = "Approve"
DENY_OPTION = "Deny"
#: Interaction id prefix that marks a pending choice as an approval request for one
#: exact tool invocation (name plus a digest of its arguments).
_APPROVAL_INTERACTION_PREFIX = "approval:"


class AnswerRejected(ValueError):
    """The answer does not fit the pending choice (not one of its options, or malformed)."""


class UnattendedSessions:
    """Owns unattended sessions and actor-scoped hosted conversation continuity."""

    def __init__(self, manager: SessionManager | None = None) -> None:
        self._manager = manager or SessionManager()

    def open(self) -> SessionCore:
        session = self._manager.create(persistent_tasks=False, warm_integrations=False)
        prepare_unattended_session(session)
        return session

    def hosted_conversation(self) -> str | None:
        """The bound actor's own conversation id, when that conversation still exists."""
        existing = hosted_conversation_id()
        if existing is not None and self._manager.has_session(existing):
            return existing
        return None

    def open_hosted_conversation(self) -> SessionCore:
        """Open a new conversation and make it the bound actor's own."""
        session = self.open()
        _remember_hosted_conversation(session.session_id)
        return session

    def resume(self, session_id: str) -> SessionCore:
        """Reload a persisted session and restore deferred questions."""
        session = self._manager.resolve(session_id, warm_integrations=False, persistent_tasks=False)
        prepare_unattended_session(session)
        return session

    def resume_conversation(self, session_id: str) -> SessionCore | None:
        """Resume one of the bound actor's conversations; ``None`` when it has no such session."""
        if not self._manager.has_session(session_id):
            return None
        return self.resume(session_id)

    def flush(self, session: SessionCore) -> None:
        """Persist the session's state now, so a reload during the turn sees it."""
        self._manager.flush(session)

    def close(self, session: SessionCore) -> None:
        self._manager.close(session, wait_for_memory_extraction=False)


def hosted_conversation_id() -> str | None:
    """The bound actor's own conversation id as stored; it may name a deleted session."""
    from config.constants.paths import session_home

    path = session_home() / "hosted-conversation"
    try:
        session_id = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return session_id or None


def _remember_hosted_conversation(session_id: str) -> None:
    """Record the actor's conversation id on the same volume as the transcript."""
    import os
    import tempfile

    from config.constants.paths import session_home

    path = session_home() / "hosted-conversation"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".hosted-conversation-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(session_id + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        from pathlib import Path

        Path(temporary).unlink(missing_ok=True)


def prepare_unattended_session(session: SessionCore) -> None:
    """A question ends the turn as a pending choice instead of waiting for an answer."""
    session.available_capabilities["ask_user_choice"] = ("deferred",)


def invocation_key(
    tool_name: str, arguments: Mapping[str, Any], *, schema: Mapping[str, Any] | None = None
) -> str:
    """Names one tool call: the tool plus a digest of the arguments that change what it does.

    An argument left out, given as ``None`` or given as its schema default is the
    same call as one that spells the default out, so a grant survives the model
    restating the call in either form.
    """
    effective = _effective_arguments(arguments, schema)
    canonical = json.dumps(effective, sort_keys=True, default=str, ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{tool_name}:{digest}"


def _effective_arguments(
    arguments: Mapping[str, Any], schema: Mapping[str, Any] | None
) -> dict[str, Any]:
    properties = schema.get("properties") if isinstance(schema, Mapping) else None
    defaults: dict[str, Any] = {}
    if isinstance(properties, Mapping):
        for name, spec in properties.items():
            if isinstance(spec, Mapping) and "default" in spec:
                defaults[name] = spec["default"]
    effective: dict[str, Any] = {}
    for name, value in arguments.items():
        if value is None:
            continue
        if name in defaults and value == defaults[name]:
            continue
        effective[name] = value
    return effective


def approval_question(
    tool_name: str,
    arguments: Mapping[str, Any],
    reason: str,
    arguments_preview: str,
    *,
    schema: Mapping[str, Any] | None = None,
) -> PendingUserChoice:
    """The Approve/Deny choice that stands in for a chat approval button, for one call."""
    details = [part for part in (reason.strip(), arguments_preview.strip()) if part]
    key = invocation_key(tool_name, arguments, schema=schema)
    return PendingUserChoice(
        title=f"Approve {tool_name}?",
        options=(APPROVE_OPTION, DENY_OPTION),
        note="\n".join(details),
        custom_answer=False,
        interaction_id=f"{_APPROVAL_INTERACTION_PREFIX}{key}",
    )


def approval_grant(pending: PendingUserChoice | None, answer: str) -> str | None:
    """The invocation key an Approve answer grants when ``pending`` is an approval question."""
    if pending is None or not pending.interaction_id.startswith(_APPROVAL_INTERACTION_PREFIX):
        return None
    if answer.strip().lower() != APPROVE_OPTION.lower():
        return None
    return pending.interaction_id[len(_APPROVAL_INTERACTION_PREFIX) :]


def choice_view(pending: PendingUserChoice) -> dict[str, Any]:
    """The pending choice as plain data a remote caller can render as a menu."""
    questions = [
        {
            "title": question.title,
            "options": list(question.options),
            "multi_select": question.multi_select,
        }
        for question in pending.items()
    ]
    return {
        "title": pending.title,
        "note": pending.note,
        "questions": questions,
        "custom_answer": pending.custom_answer,
    }


def answer_pending_choice(session: SessionCore, answer: str) -> str:
    """Turn the caller's answer into the next user message and clear the pending choice.

    One question takes an option number, an option label, or free text when the
    choice allows it. Several questions take a JSON object keyed by question
    title or position. Raises :class:`AnswerRejected` when the answer does not fit.
    """
    pending = session.pending_user_choice
    if pending is None:
        session.ask_user_rounds = 0
        return answer
    questions = pending.items()
    answers: tuple[str, ...]
    if len(questions) == 1:
        answers = (_selected_answer(questions[0], answer, allow_custom=pending.custom_answer),)
    else:
        answers = _batch_answers(questions, answer, allow_custom=pending.custom_answer)
    session.pending_user_choice = None
    session.questions_already_answered.update(
        question_key(question.title) for question in questions
    )
    return format_ask_user_answers(questions, answers)


def _batch_answers(
    questions: tuple[AskUserQuestion, ...], answer: str, *, allow_custom: bool
) -> tuple[str, ...]:
    try:
        supplied = json.loads(answer)
    except json.JSONDecodeError as exc:
        raise AnswerRejected("Several questions are open; answer with a JSON object.") from exc
    if not isinstance(supplied, dict):
        raise AnswerRejected("Answers for several questions must be a JSON object.")
    values: list[str] = []
    for index, question in enumerate(questions, start=1):
        candidates = (question.label, question.title, str(index))
        value = next((supplied[key] for key in candidates if key in supplied), None)
        if value is None:
            raise AnswerRejected(f"Missing answer for {question.title!r}.")
        values.append(_selected_answer(question, str(value), allow_custom=allow_custom))
    return tuple(values)


def _selected_answer(question: AskUserQuestion, raw: str, *, allow_custom: bool) -> str:
    """Resolve numbers and labels to options; keep free text only when allowed."""
    picks = [part.strip() for part in raw.split(",")] if question.multi_select else [raw.strip()]
    chosen: list[str] = []
    for pick in picks:
        option = _match_option(question, pick)
        if option is None and not allow_custom:
            raise AnswerRejected(f"{pick!r} is not one of the options for {question.title!r}.")
        chosen.append(option if option is not None else pick)
    if not chosen or not all(chosen):
        raise AnswerRejected(f"An answer is required for {question.title!r}.")
    return ", ".join(chosen)


def _match_option(question: AskUserQuestion, pick: str) -> str | None:
    if pick.isdigit():
        index = int(pick)
        if 1 <= index <= len(question.options):
            return question.options[index - 1]
        return None
    lowered = pick.lower()
    for option in question.options:
        if option.lower() == lowered:
            return option
    return None


__all__ = [
    "APPROVE_OPTION",
    "DENY_OPTION",
    "AnswerRejected",
    "UnattendedSessions",
    "hosted_conversation_id",
    "answer_pending_choice",
    "approval_grant",
    "approval_question",
    "choice_view",
    "invocation_key",
    "prepare_unattended_session",
]

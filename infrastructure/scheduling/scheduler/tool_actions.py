"""Describe a scheduled run's tool calls that changed something as short action lines.

A call is an action when its tool declares a mutating or external side effect
and the call succeeded, or when the tool reports a ``work_outcome`` for the
operation it attempted. Tools that declare no level, read-only tools,
bookkeeping tools and calls their tool reports as reads are never recorded. A
line names the tool, what it acted on (a command's leading words, repository,
pull request, branch, channel) and what it produced (a summary, URL, commit
SHA, work outcome). A command keeps only its leading words, up to the first
option, quoted text or shell operator, so option values such as a comment body
never reach the record; every value is credential-redacted before it is
shortened.
"""

from __future__ import annotations

import logging
import re
import shlex
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from core.tool import (
    CALL_SIDE_EFFECT_LEVEL_KEY,
    SideEffectLevel,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolRole,
)
from infrastructure.safety.secret_redaction import redact_text
from infrastructure.scheduling.scheduler.run_activity import (
    ACTION_MAX_CHARS,
    compact_text,
    current_run_activity,
)

logger = logging.getLogger(__name__)

_RECORDED_LEVELS = frozenset({SideEffectLevel.MUTATING, SideEffectLevel.EXTERNAL})
#: Levels a call may report for itself that keep it out of the record.
_UNRECORDED_CALL_LEVELS = frozenset({SideEffectLevel.NONE.value, SideEffectLevel.READ_ONLY.value})
#: Arguments whose value is what ran: a shell command, gh arguments, a slash command.
_COMMAND_KEYS = ("command", "args")
#: A word a command is shown with: a program, subcommand, number, ref, path or
#: URL without a query. Options, quoted text, assignments and operators are not.
_COMMAND_WORD = re.compile(r"[\w./][\w.:/#+,-]{0,99}")
_ENV_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
#: Separators after a leading ``cd <dir>``, whose command is the one worth naming.
_CD_SEPARATORS = frozenset({"&&", ";"})
_COMMAND_MAX_WORDS = 6
#: Arguments that name what a call acted on, in display order.
_TARGET_KEYS = (
    "owner",
    "repo",
    "repository",
    "pull_request",
    "pr_number",
    "pr_url",
    "issue_number",
    "alert_type",
    "alert_number",
    "branch",
    "ref",
    "channel",
    "channel_id",
    "chat_id",
    "target",
)
#: Result fields that name what a call produced, in display order.
_RESULT_KEYS = (
    "url",
    "html_url",
    "comment_url",
    "pr_url",
    "issue_url",
    "pr_number",
    "branch_name",
    "commit_sha",
    "fix_head_sha",
    "head_sha",
    "sha",
)
_VALUE_MAX_CHARS = 60
_SUMMARY_MAX_CHARS = 100
#: A command keeps at least this much of a line other values have filled.
_COMMAND_MIN_CHARS = 40


def _text(value: Any) -> str:
    """A scalar or a list of scalars as one redacted line; anything else is empty.

    The whole value is redacted before any cut, so a cut never leaves part of a
    credential the patterns no longer recognize.
    """
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, list | tuple):
        if not all(isinstance(item, str | int | float) for item in value):
            return ""
        value = " ".join(str(item) for item in value)
    elif not isinstance(value, str | int | float):
        return ""
    return " ".join(redact_text(str(value)).split())


def _command_tokens(value: Any) -> list[str]:
    """A shell string split on whitespace with quotes kept, or an argv list as given."""
    if isinstance(value, str):
        lexer = shlex.shlex(value, posix=False)
        lexer.whitespace_split = True
        try:
            return list(lexer)
        except ValueError:
            return value.split()
    if isinstance(value, list | tuple) and all(isinstance(item, str) for item in value):
        return list(value)
    return []


def _command_words(value: Any) -> str:
    """A command's leading words, ending in ``…`` when anything after them was dropped.

    A leading ``cd <dir> &&`` and environment assignments are skipped; the words
    stop at the first token that is not a plain word (an option, quoted text, an
    operator), so payload-bearing values are never shown.
    """
    tokens = _command_tokens(value)
    while tokens and tokens[0] == "cd":
        separator = next((i for i, token in enumerate(tokens) if token in _CD_SEPARATORS), None)
        if separator is None:
            break
        tokens = tokens[separator + 1 :]
    while tokens and _ENV_ASSIGNMENT.match(tokens[0]):
        tokens = tokens[1:]
    words: list[str] = []
    for token in tokens[:_COMMAND_MAX_WORDS]:
        if not _COMMAND_WORD.fullmatch(token):
            break
        words.append(token)
    if not words:
        return ""
    shown = _text(" ".join(words))
    return f"{shown} …" if len(words) < len(tokens) else shown


def _fields(source: Mapping[str, Any], keys: Iterable[str], seen: set[str]) -> list[str]:
    """``key=value`` for each listed key ``source`` holds, skipping values already shown."""
    fields = []
    for key in keys:
        value = _text(source.get(key))
        if value and value not in seen:
            seen.add(value)
            fields.append(f"{key}={compact_text(value, _VALUE_MAX_CHARS)}")
    return fields


def _succeeded(result: ToolExecutionResult, details: Mapping[str, Any]) -> bool:
    return not (result.is_error or details.get("ok") is False or details.get("success") is False)


def _outcome_fields(outcome: Mapping[str, Any], seen: set[str]) -> list[str]:
    """The operation, its verified status and the evidence that names what changed."""
    status = _text(outcome.get("status"))
    kind = _text(outcome.get("error_kind"))
    parts = [part for part in (_text(outcome.get("operation")), status) if part]
    if kind:
        parts.append(f"({compact_text(kind, _VALUE_MAX_CHARS)})")
    evidence = outcome.get("evidence")
    if isinstance(evidence, Mapping):
        parts.extend(_fields(evidence, _RESULT_KEYS, seen))
    return parts


def describe_tool_action(request: ToolExecutionRequest, result: ToolExecutionResult) -> str | None:
    """One line naming what a call changed, or ``None`` when the call changed nothing."""
    tool = request.tool
    if getattr(tool, "side_effect_level", None) not in _RECORDED_LEVELS:
        return None
    if getattr(tool, "role", None) is ToolRole.BOOKKEEPING:
        return None
    details: Mapping[str, Any] = result.details if isinstance(result.details, Mapping) else {}
    if details.get(CALL_SIDE_EFFECT_LEVEL_KEY) in _UNRECORDED_CALL_LEVELS:
        return None
    outcome = details.get("work_outcome")
    if not isinstance(outcome, Mapping):
        outcome = None
        if not _succeeded(result, details):
            return None
    command = " ".join(
        part
        for part in (_command_words(request.arguments.get(key)) for key in _COMMAND_KEYS)
        if part
    )
    seen = {command} if command else set()
    targets = _fields(request.arguments, _TARGET_KEYS, seen)
    produced = []
    summary = _text(details.get("summary"))
    if summary:
        produced.append(compact_text(summary, _SUMMARY_MAX_CHARS))
    produced.extend(_fields(details, _RESULT_KEYS, seen))
    if outcome is not None:
        produced.extend(_outcome_fields(outcome, seen))
    name = request.tool_call.name
    middle = " ".join(targets)
    tail = f" → {' '.join(produced)}" if produced else ""
    room = ACTION_MAX_CHARS - len(name) - len(middle) - len(tail) - 2
    head = " ".join(
        part
        for part in (name, compact_text(command, max(room, _COMMAND_MIN_CHARS)), middle)
        if part
    )
    return compact_text(head + tail, ACTION_MAX_CHARS)


def bound_action_hook() -> Callable[[ToolExecutionRequest, ToolExecutionResult], None] | None:
    """An ``after_tool_call`` hook recording into the attempt bound on this thread, if any.

    The hook holds the activity itself, so it records from whichever thread runs
    the tool. It never raises: a description failure must not touch the call.
    """
    activity = current_run_activity()
    if activity is None:
        return None

    def record(request: ToolExecutionRequest, result: ToolExecutionResult) -> None:
        try:
            action = describe_tool_action(request, result)
        except Exception:
            logger.debug("Could not describe tool call %s", request.tool_call.name, exc_info=True)
            return
        if action:
            activity.add_action(action)

    return record


__all__ = ["bound_action_hook", "describe_tool_action"]

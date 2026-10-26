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


#: Characters that end a simple command when unquoted: operators, groups, newlines.
_SHELL_PUNCTUATION = "();<>|&\n"
#: Shells whose ``-c`` script holds commands of its own.
_SHELLS = frozenset({"bash", "dash", "sh", "zsh"})
#: Wrappers that run the command after them: their flags, then their options that take a
#: value. Any other option makes the command unknown, so its writes are never claimed.
_COMMAND_WRAPPERS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "command": (frozenset({"-p"}), frozenset()),
    "env": (
        frozenset({"-i", "--ignore-environment"}),
        frozenset({"-C", "-u", "--chdir", "--unset"}),
    ),
    "exec": (frozenset({"-c", "-l"}), frozenset({"-a"})),
    "nohup": (frozenset(), frozenset()),
    "sudo": (
        frozenset({"-E", "-H", "-S", "-n", "--non-interactive", "--preserve-env", "--stdin"}),
        frozenset({"-g", "-u", "--group", "--user"}),
    ),
    "time": (frozenset({"-p", "--portability"}), frozenset({"-f", "-o", "--format", "--output"})),
}
#: How deep ``sh -c '…'`` scripts are followed.
_MAX_SCRIPT_DEPTH = 2
#: Global ``git`` options that take the next word as their value.
_GIT_VALUE_OPTIONS = frozenset(
    {"-C", "-c", "--config-env", "--exec-path", "--git-dir", "--namespace", "--work-tree"}
)
_GIT_DRY_RUN = frozenset({"-n", "--dry-run"})
#: Global ``gh`` options that take the next word as their value.
_GH_VALUE_OPTIONS = frozenset({"-R", "--repo", "--hostname"})
#: ``gh <group> <verb>`` commands that change GitHub.
_GH_WRITE_VERBS: dict[str, frozenset[str]] = {
    "issue": frozenset({"close", "comment", "create", "edit", "reopen"}),
    "pr": frozenset({"close", "comment", "create", "edit", "merge", "ready", "reopen", "review"}),
}
_GH_API_WRITE_METHODS = frozenset({"DELETE", "PATCH", "POST", "PUT"})
#: ``gh api`` options that send a body, which makes the default method POST.
_GH_API_BODY_OPTIONS = frozenset({"-f", "-F", "--field", "--raw-field", "--input"})


def call_failed(result: ToolExecutionResult) -> bool:
    """Whether a call errored or reported ``ok`` or ``success`` false, e.g. a nonzero exit."""
    details: Mapping[str, Any] = result.details if isinstance(result.details, Mapping) else {}
    return not _succeeded(result, details)


def is_remote_write(request: ToolExecutionRequest, result: ToolExecutionResult) -> bool:
    """Whether a successful ``shell_run`` or ``github_cli`` call pushed or changed GitHub.

    A ``git push``, a pull-request or issue change (``gh pr create``, ``gh pr
    comment``, ...) or a ``gh api`` call that sends a write counts when it is the
    program a shell command runs, including inside ``sh -c '…'``. Reads, failed
    calls and text that only mentions such a command never count.
    """
    if call_failed(result):
        return False
    name = request.tool_call.name
    if name == "github_cli":
        return _gh_writes(_command_tokens(request.arguments.get("args")))
    command = request.arguments.get("command")
    return name == "shell_run" and isinstance(command, str) and _script_writes(command, 0)


def _script_writes(script: str, depth: int) -> bool:
    """Whether a simple command of ``script`` runs a push or a GitHub write."""
    for words in _simple_commands(script):
        program, args = _program(words)
        if program == "git" and _git_pushes(args):
            return True
        if program == "gh" and _gh_writes(args):
            return True
        if program in _SHELLS and depth < _MAX_SCRIPT_DEPTH:
            inner = _shell_script(args)
            if inner and _script_writes(inner, depth + 1):
                return True
    return False


def _simple_commands(script: str) -> list[list[str]]:
    """The words of each simple command in ``script``; none when it does not parse."""
    lexer = shlex.shlex(script, posix=True, punctuation_chars=_SHELL_PUNCTUATION)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    commands: list[list[str]] = [[]]
    try:
        for token in lexer:
            if token and all(char in _SHELL_PUNCTUATION for char in token):
                commands.append([])
            else:
                commands[-1].append(token)
    except ValueError:
        return []
    return [words for words in commands if words]


def _program(words: list[str]) -> tuple[str, list[str]]:
    """The program a simple command runs, past assignments and wrappers, and its arguments.

    Only wrapper options known to still run the command are skipped, with their
    values (``env -u NAME``, ``sudo --user=bot``). Any other option, such as
    ``sudo -l`` or ``env -S``, leaves the program unknown: a write is never
    claimed for a command that may not have run.
    """
    index = 0
    while index < len(words):
        word = words[index]
        program = word.rsplit("/", 1)[-1]
        index += 1
        if _ENV_ASSIGNMENT.match(word):
            continue
        options = _COMMAND_WRAPPERS.get(program)
        if options is None:
            return program, words[index:]
        flags, value_options = options
        while index < len(words) and words[index].startswith("-"):
            option, _, attached = words[index].partition("=")
            index += 1
            if option == "--":
                break
            if option in value_options:
                index += 0 if attached else 1
            elif option not in flags or attached:
                return "", []
    return "", []


def _shell_script(args: list[str]) -> str:
    """The script a shell runs with ``-c`` (options such as ``-e`` may come first), else ""."""
    for index, word in enumerate(args):
        if not word.startswith("-"):
            return ""
        if not word.startswith("--") and "c" in word[1:]:
            return args[index + 1] if index + 1 < len(args) else ""
    return ""


def _git_pushes(args: list[str]) -> bool:
    """``git [global options] push`` without a dry run."""
    index = 0
    while index < len(args) and args[index].startswith("-"):
        index += 2 if args[index] in _GIT_VALUE_OPTIONS else 1
    if index >= len(args) or args[index] != "push":
        return False
    return not _GIT_DRY_RUN.intersection(args[index + 1 :])


def _gh_writes(args: list[str]) -> bool:
    """``gh`` arguments that change GitHub: a pull-request or issue write, or an API write."""
    positionals: list[str] = []
    method = ""
    sends_body = False
    index = 0
    while index < len(args):
        token = args[index]
        if token in ("-X", "--method"):
            method = args[index + 1].upper() if index + 1 < len(args) else ""
            index += 2
            continue
        if token in _GH_VALUE_OPTIONS:
            index += 2
            continue
        if token.startswith("--method="):
            method = token.split("=", 1)[1].upper()
        elif token in _GH_API_BODY_OPTIONS or token.startswith(("--field=", "--raw-field=")):
            sends_body = True
        elif not token.startswith("-"):
            positionals.append(token)
        index += 1
    if not positionals:
        return False
    if positionals[0] == "api":
        return method in _GH_API_WRITE_METHODS or (sends_body and not method)
    verbs = _GH_WRITE_VERBS.get(positionals[0], frozenset())
    return len(positionals) > 1 and positionals[1] in verbs


__all__ = ["bound_action_hook", "call_failed", "describe_tool_action", "is_remote_write"]

"""Plain-text tool activity shared by the shell and the hosted-prompt feed.

Rich painting stays in the interactive shell. This module is the wording both
sides use, so a gateway tool line cannot drift from the local one. Shell-only
command folding (heredocs) stays in the shell.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any

from config.constants.gateway import (
    PROMPT_PROGRESS_KIND_PLAN,
    PROMPT_PROGRESS_KIND_TOOL,
)
from core.agent_harness.task_plan.plan import parse_task_plan
from core.agent_harness.task_plan.progress import format_task_plan_plain
from infrastructure.observability.trace.redaction import redact_sensitive
from infrastructure.safety.terminal_output import strip_terminal_controls

_PREVIEW_MAX_CHARS = 180
_VALUE_MAX_CHARS = 64
_GH_VERBOSE_VALUE_FLAGS = frozenset({"--jq", "--template", "-t"})
_GH_PRIVATE_VALUE_FLAGS = frozenset({"-H", "--header", "-f", "-F", "--field", "--raw-field"})
_GH_COLLAPSED_VALUE_FLAGS = _GH_VERBOSE_VALUE_FLAGS | _GH_PRIVATE_VALUE_FLAGS
_GH_GLOBAL_VALUE_FLAGS = frozenset({"-R", "--repo", "--hostname"})
_GH_SUMMARY_VALUE_FLAGS = _GH_GLOBAL_VALUE_FLAGS | _GH_VERBOSE_VALUE_FLAGS
_GH_SAFE_SUBCOMMANDS = {
    "issue": frozenset(
        {
            "close",
            "comment",
            "create",
            "delete",
            "edit",
            "list",
            "reopen",
            "status",
            "transfer",
            "view",
        }
    ),
    "pr": frozenset(
        {
            "checks",
            "checkout",
            "close",
            "comment",
            "create",
            "diff",
            "edit",
            "list",
            "merge",
            "ready",
            "reopen",
            "review",
            "status",
            "view",
        }
    ),
    "release": frozenset({"list", "view"}),
    "repo": frozenset({"clone", "create", "fork", "list", "sync", "view"}),
    "run": frozenset({"list", "view"}),
    "search": frozenset({"code", "commits", "issues", "prs", "repos"}),
    "workflow": frozenset({"list", "view"}),
}
_EXECUTION_KEYS = frozenset({"runtime_metadata", "timeout"})
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)
_ASK_USER = "ask_user_choice"
_UPDATE_PLAN = "update_plan"
_SLASH_INVOKE = "slash_invoke"
_CHOOSE_COMMAND = "/choose"
_HOSTED_TOOL_SUMMARIES = {
    "cli_exec": ("OpenSRE CLI", "Run command"),
    "code_implement": ("Code", "Implement changes"),
    "execute_python_code": ("Python", "Run code"),
    _SLASH_INVOKE: ("OpenSRE", "Run slash command"),
}
_SHELL_SUMMARY_MAX_CHARS = 512
_SHELL_SUMMARY_MAX_TOKENS = 16


@dataclass(frozen=True, slots=True)
class HostedActivity:
    """One progress line for the shell's view of a hosted-gateway prompt."""

    kind: str
    text: str


def format_hosted_activity(tool_name: str, tool_input: object) -> HostedActivity | None:
    """Compact activity for a gateway tool start, or ``None`` when it is not a row.

    ``update_plan`` is a checklist (``kind="plan"``), not a tool line.
    ``ask_user_choice`` and the private ``/choose`` turn are silent: the menu
    owns that interaction.
    """
    name = tool_name.strip()
    args = tool_input if isinstance(tool_input, dict) else {}
    if not name or name == _ASK_USER or _is_choose(name, args):
        return None
    if name == _UPDATE_PLAN:
        plan, error = parse_task_plan(args)
        if plan is None or error is not None:
            return None
        return HostedActivity(PROMPT_PROGRESS_KIND_PLAN, format_task_plan_plain(plan))
    if name == "github_cli":
        label, content = _hosted_github_cli_activity(args)
    elif name == "shell_run":
        label, content = _shell_run_activity(args)
    else:
        label, content = _hosted_generic_tool_activity(name)
    text = f"{label} · {content}" if content else label
    return HostedActivity(PROMPT_PROGRESS_KIND_TOOL, text)


def github_cli_activity(args: dict[str, Any]) -> tuple[str, str]:
    """Return the local command display with private and verbose values collapsed."""
    command = ["gh"]
    repo = strip_terminal_controls(str(args.get("repo", "")).strip())
    if repo:
        command.extend(["-R", repo])
    command.extend(_compact_gh_args(args.get("args")))
    preview = shlex.join(command).replace("'…'", "…")
    return "GitHub CLI", preview


def generic_tool_activity(tool_name: str, args: dict[str, Any]) -> tuple[str, str]:
    """Humanized tool name plus a bounded ``key: value`` preview.

    Execution controls and sensitive keys are omitted. Values are previews,
    never ``str(dict)``.
    """
    safe_args = {
        str(key): value
        for key, value in args.items()
        if key not in _EXECUTION_KEYS and not is_sensitive_activity_key(key)
    }
    redacted = redact_sensitive(safe_args)
    content = " · ".join(
        f"{key}: {_value_preview(value)}" for key, value in sorted(redacted.items())
    )
    return tool_name.replace("_", " "), bounded_activity_preview(content)


def bounded_activity_preview(value: str, *, limit: int = _PREVIEW_MAX_CHARS) -> str:
    """One collapsed line, cut with an ellipsis only past ``limit``."""
    collapsed = " ".join(value.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def is_sensitive_activity_key(key: object) -> bool:
    """True when a tool-argument name looks like a credential."""
    normalized = str(key).casefold().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _is_choose(tool_name: str, args: dict[str, Any]) -> bool:
    return tool_name == _SLASH_INVOKE and str(args.get("command", "")).strip() == _CHOOSE_COMMAND


def _github_command_summary(raw_args: object) -> str:
    """Return only allowlisted command words; values and targets never persist."""
    if not isinstance(raw_args, list):
        return "gh command"
    tokens = [strip_terminal_controls(str(item).strip()) for item in raw_args]
    positionals: list[str] = []
    index = 0
    while index < len(tokens) and len(positionals) < 2:
        token = tokens[index]
        if not token:
            index += 1
            continue
        if token == "--":
            index += 1
            continue
        if token.startswith("-"):
            name, separator, _value = token.partition("=")
            if not separator and name in _GH_SUMMARY_VALUE_FLAGS and index + 1 < len(tokens):
                index += 2
                continue
            index += 1
            continue
        positionals.append(token.casefold())
        index += 1
    if not positionals:
        return "gh command"
    command = positionals[0]
    if command == "api":
        return "gh api request"
    allowed = _GH_SAFE_SUBCOMMANDS.get(command)
    if allowed is None:
        return "gh command"
    if len(positionals) > 1 and positionals[1] in allowed:
        return f"gh {command} {positionals[1]}"
    return f"gh {command}"


def _hosted_github_cli_activity(args: dict[str, Any]) -> tuple[str, str]:
    """Describe a hosted ``gh`` call without retaining free-form argument values."""
    return "GitHub CLI", _github_command_summary(args.get("args"))


def _hosted_generic_tool_activity(tool_name: str) -> tuple[str, str]:
    """Describe a hosted tool call without retaining any argument values."""
    summary = _HOSTED_TOOL_SUMMARIES.get(tool_name)
    if summary is not None:
        return summary
    label = bounded_activity_preview(
        strip_terminal_controls(tool_name).replace("_", " "), limit=_VALUE_MAX_CHARS
    )
    return label or "Tool", "Run tool"


def _compact_gh_args(raw_args: object) -> list[str]:
    """Retain the local command shape while hiding private and verbose values."""
    if not isinstance(raw_args, list):
        return []
    tokens = [strip_terminal_controls(str(item).strip()) for item in raw_args]
    compact: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        attached_flag = _attached_gh_value_flag(token)
        if attached_flag is not None:
            compact.extend([attached_flag, "…"])
            index += 1
            continue
        compact.append(token)
        if token in _GH_COLLAPSED_VALUE_FLAGS and index + 1 < len(tokens):
            compact.append("…")
            index += 2
            continue
        index += 1
    return compact


def _attached_gh_value_flag(token: str) -> str | None:
    """Return an attached-value flag without retaining its free-form value."""
    for flag in _GH_COLLAPSED_VALUE_FLAGS:
        if flag.startswith("--"):
            if token.startswith(f"{flag}="):
                return flag
        elif token.startswith(flag) and len(token) > len(flag):
            return flag
    return None


def _shell_run_activity(args: dict[str, Any]) -> tuple[str, str]:
    """Describe a shell call without persisting its free-form command text."""
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return "Shell", "Run command"
    return "Shell", _shell_command_summary(command)


def _shell_command_summary(command: str) -> str:
    """Return a static summary for a small allowlist of recognizable commands."""
    tokens = _shell_prefix_tokens(command)
    if not tokens:
        return "Run command"

    if tokens[0].rsplit("/", 1)[-1].casefold() == "cd":
        try:
            separator = tokens.index("&&")
        except ValueError:
            return "Run command"
        tokens = tokens[separator + 1 :]
        if not tokens:
            return "Run command"

    executable = tokens[0].rsplit("/", 1)[-1].casefold()
    subcommand = tokens[1].casefold() if len(tokens) > 1 else ""
    if executable == "git":
        return {
            "status": "Check Git status",
            "diff": "Inspect Git changes",
            "log": "Inspect Git history",
            "show": "Inspect Git history",
        }.get(subcommand, "Run Git command")
    if executable in {"pytest", "py.test"}:
        return "Run tests"
    tail = tuple(token.casefold() for token in tokens[1:])
    if executable in {"python", "python3"} and (
        subcommand == "pytest" or tail[:2] == ("-m", "pytest")
    ):
        return "Run tests"
    if executable == "uv" and (
        tail[:2] == ("run", "pytest")
        or (
            len(tail) >= 4
            and tail[0] == "run"
            and tail[1].rsplit("/", 1)[-1] in {"python", "python3"}
            and tail[2:4] == ("-m", "pytest")
        )
    ):
        return "Run tests"
    if executable in {"rg", "grep", "find", "ls", "dir", "pwd"}:
        return "Inspect workspace"
    if executable in {"curl", "wget"}:
        return "Run network command"
    if executable in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        return "Run PowerShell command"
    if executable in {"cmd", "cmd.exe"}:
        return "Run Windows command"
    return "Run command"


def _shell_prefix_tokens(command: str) -> list[str]:
    """Tokenize only enough of a shell command to identify its static shape."""
    lexer = shlex.shlex(
        command[:_SHELL_SUMMARY_MAX_CHARS],
        posix=True,
        punctuation_chars=";&|",
    )
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens: list[str] = []
    try:
        while len(tokens) < _SHELL_SUMMARY_MAX_TOKENS:
            token = lexer.get_token()
            if token is None:
                break
            tokens.append(token)
    except ValueError:
        # A truncated or malformed trailing value does not invalidate command
        # words that were already read from the bounded prefix.
        pass
    return tokens


def _value_preview(value: Any) -> str:
    if isinstance(value, dict):
        keys = [str(key) for key in value if not is_sensitive_activity_key(key)]
        listed = ", ".join(keys[:4])
        extra = f" +{len(keys) - 4}" if len(keys) > 4 else ""
        return f"fields {listed}{extra}"
    if isinstance(value, (list, tuple)):
        items = [bounded_activity_preview(str(item), limit=24) for item in value[:4]]
        extra = f" +{len(value) - 4}" if len(value) > 4 else ""
        return ", ".join(items) + extra
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    return bounded_activity_preview(str(value), limit=_VALUE_MAX_CHARS)


__all__ = [
    "HostedActivity",
    "bounded_activity_preview",
    "format_hosted_activity",
    "generic_tool_activity",
    "github_cli_activity",
    "is_sensitive_activity_key",
]

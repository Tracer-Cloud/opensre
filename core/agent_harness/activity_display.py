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
        label, content = github_cli_activity(args)
    else:
        label, content = generic_tool_activity(name, args)
    text = f"{label} · {content}" if content else label
    return HostedActivity(PROMPT_PROGRESS_KIND_TOOL, text)


def github_cli_activity(args: dict[str, Any]) -> tuple[str, str]:
    """``(GitHub CLI, gh …)`` with verbose flag bodies and secrets collapsed."""
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


def _compact_gh_args(raw_args: object) -> list[str]:
    """Retain the command shape while hiding verbose expression bodies."""
    if not isinstance(raw_args, list):
        return []
    tokens = [strip_terminal_controls(str(item).strip()) for item in raw_args]
    compact: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        compact.append(token)
        if token in _GH_VERBOSE_VALUE_FLAGS and index + 1 < len(tokens):
            compact.append("…")
            index += 2
            continue
        if token in {"-H", "--header"} and index + 1 < len(tokens):
            header = tokens[index + 1]
            shown = (
                "…"
                if is_sensitive_activity_key(header)
                else bounded_activity_preview(header, limit=40)
            )
            compact.append(shown)
            index += 2
            continue
        if index + 1 < len(tokens) and token in {"-f", "-F", "--field", "--raw-field"}:
            compact.append(bounded_activity_preview(tokens[index + 1], limit=_VALUE_MAX_CHARS))
            index += 2
            continue
        index += 1
    return compact


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

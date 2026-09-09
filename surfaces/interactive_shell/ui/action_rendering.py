"""Rendering for the shell tool-calling turn.

This module owns the terminal-facing action observer. Planner tool calls are
internal state by default: the observer records them for history/storage while
the concrete action executors render user-facing command output. The execution
orchestration that drives it lives in
:func:`interactive_shell.runtime.action_turn.run_action_tool_turn`.

Keeping rendering here means the shell turn-entry adapter stays focused on
binding core ports while terminal formatting stays in ``ui/``.
"""

from __future__ import annotations

import ast
import contextlib
import re
import shlex
from typing import Any

from rich.console import Console
from rich.text import Text

from core.agent_harness.spi.accounting import SELF_RECORDING_ACTION_TOOL_NAMES
from core.agent_harness.spi.task_plan import is_plan_diagnosis_prose
from infrastructure.observability.trace.redaction import redact_sensitive
from infrastructure.safety.terminal_output import strip_terminal_controls
from infrastructure.terminal.theme import (
    BOLD_SKILL,
    DIM,
    TEXT,
)
from infrastructure.text import is_data_blob
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.runtime.core.state import SpinnerState
from surfaces.interactive_shell.session.terminal_session import ActionLogEntry
from surfaces.interactive_shell.ui.action_log import flush_action_log
from surfaces.interactive_shell.ui.streaming import render_note_block
from surfaces.shared.terminal.output.console_state import get_turn_spinner
from tools.interactive_shell.action_names import ActionToolName
from tools.interactive_shell.shell.display import format_shell_command_for_display

# Tool labels whose payload is a runnable command.
_COMMAND_TOOL_LABELS: frozenset[str] = frozenset({"Execute", "GitHub CLI", "opensre"})

# Leads every tool-call line so a call reads apart from the ``Ω`` reply and the
# ``[n] ❯`` user row — the call → result → reply hierarchy Claude Code / Droid use.
_TOOL_CALL_MARKER = "⏺"

# Tools whose preview is just ``(label, single-arg)``. The display content is the
# stripped string value of that single argument. Anything that needs to combine
# multiple arguments (``slash_invoke``, ``synthetic_run``) keeps a custom branch
# in :func:`tool_call_display`.
_TOOL_PREVIEW_MAX_CHARS = 180
_TOOL_VALUE_MAX_CHARS = 64
_GH_VERBOSE_VALUE_FLAGS = frozenset({"--jq", "--template", "-t"})
_GENERIC_EXECUTION_KEYS = frozenset({"runtime_metadata", "timeout"})
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)
_PYTHON_URL_RE = re.compile(r"https?://[^\s'\"`]+")

_SIMPLE_TOOL_LABELS: dict[str, tuple[str, str]] = {
    ActionToolName.LLM_SET_PROVIDER: ("LLM provider", "target"),
    ActionToolName.TASK_CANCEL: ("cancel task", "target"),
    ActionToolName.CLI_EXEC: ("opensre", "payload"),
    ActionToolName.CODE_IMPLEMENT: ("implementation", "task"),
    ActionToolName.SHELL_RUN: ("Execute", "command"),
}

#: Tools that must not appear in the post-execution plan work log (plan/UI plumbing).
_SKIP_PLAN_WORK_TOOLS: frozenset[str] = frozenset(
    {
        ActionToolName.UPDATE_PLAN,
        ActionToolName.ASK_USER_CHOICE,
    }
)

#: Tools that render their own dedicated UI. The generic live tool-call preview
#: is suppressed for these so it does not duplicate that UI as a wall of text.
_SELF_RENDERING_TOOLS: frozenset[str] = frozenset(
    {
        ActionToolName.ASK_USER_CHOICE,
        # shell_run / cli_exec stream their own ``$ <command>`` + output during
        # execution, and the running tool is folded into the status spinner row —
        # so a static ``Execute``/``opensre`` header here would print the command
        # a third time. Suppress it; the status row and the ``$`` line are enough.
        ActionToolName.SHELL_RUN,
        ActionToolName.CLI_EXEC,
    }
)

#: Tools that stream their own ``$ <command>`` line during execution. The live
#: status row names the action only (``Execute``) rather than repeating the
#: command, so the command is not shown twice while it runs.
_COMMAND_STREAMING_TOOLS: frozenset[str] = frozenset(
    {
        ActionToolName.SHELL_RUN,
        ActionToolName.CLI_EXEC,
    }
)


def _tool_event_id(data: dict[str, Any]) -> str:
    """Stable id for one tool call, or empty when the event omitted it."""
    return str(data.get("id") or data.get("tool_call_id") or "").strip()


def _is_internal_choice_command(name: str, data: dict[str, Any]) -> bool:
    """True for the private slash turn that opens the choice picker."""
    if name != ActionToolName.SLASH_INVOKE:
        return False
    args = data.get("input")
    return isinstance(args, dict) and str(args.get("command", "")).strip() == "/choose"


def _bounded_preview(value: str, *, limit: int = _TOOL_PREVIEW_MAX_CHARS) -> str:
    """Keep live progress on one useful terminal line."""
    collapsed = " ".join(value.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _preview_from_result_fields(payload: dict[str, Any]) -> str:
    """Pull the one user-facing field from a tool-result dict, or empty."""
    for key in ("response_text", "summary"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip() and not is_data_blob(value):
            return value.strip()
    stdout = payload.get("stdout")
    if (
        payload.get("ok")
        and isinstance(stdout, str)
        and stdout.strip()
        and not is_data_blob(stdout)
    ):
        return stdout.strip()
    error = payload.get("error")
    if error:
        return str(error).strip()
    return ""


def _tool_result_preview(output: object) -> str:
    """User-facing result text for a ``↳`` child, or empty for model-only data."""
    if isinstance(output, dict):
        return _preview_from_result_fields(output)
    details = getattr(output, "details", None)
    if isinstance(details, dict):
        preview = _preview_from_result_fields(details)
        if preview:
            return preview
    if isinstance(output, str):
        stripped = output.strip()
        return "" if not stripped or is_data_blob(stripped) else stripped
    content = getattr(output, "content", None)
    if isinstance(content, str) and content.strip() and not is_data_blob(content):
        return content.strip()
    return ""


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).casefold().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


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
            compact.append("…" if _is_sensitive_key(header) else _bounded_preview(header, limit=40))
            index += 2
            continue
        if index + 1 < len(tokens) and token in {"-f", "-F", "--field", "--raw-field"}:
            compact.append(_bounded_preview(tokens[index + 1], limit=_TOOL_VALUE_MAX_CHARS))
            index += 2
            continue
        index += 1
    return compact


def _github_cli_display(args: dict[str, Any]) -> tuple[str, str]:
    command = ["gh"]
    repo = strip_terminal_controls(str(args.get("repo", "")).strip())
    if repo:
        command.extend(["-R", repo])
    command.extend(_compact_gh_args(args.get("args")))
    preview = shlex.join(command).replace("'…'", "…")
    return "GitHub CLI", _bounded_preview(preview)


def _python_execution_display(args: dict[str, Any]) -> tuple[str, str]:
    details = ["run analysis"]
    if args.get("allow_network") is True:
        details.append("network enabled")
    code = str(args.get("code", "") or "")
    targets = _python_network_targets(code)
    if targets:
        details.append(f"target: {', '.join(targets)}")
    outputs = _python_output_fields(code)
    if outputs:
        details.append(f"outputs: {', '.join(outputs)}")
    inputs = args.get("inputs")
    if isinstance(inputs, dict):
        safe_inputs = redact_sensitive(
            {
                str(key): value
                for key, value in inputs.items()
                if key != "opensre_runtime" and not _is_sensitive_key(key)
            }
        )
        rendered_inputs = [
            f"{key}={_bounded_preview(str(value), limit=40)}"
            for key, value in sorted(safe_inputs.items())
        ]
        if rendered_inputs:
            details.append(f"inputs: {', '.join(rendered_inputs)}")
    else:
        referenced_inputs = _python_referenced_inputs(code)
        if referenced_inputs:
            details.append(f"inputs: {', '.join(referenced_inputs)}")
    return "Python", _bounded_preview(" · ".join(details), limit=240)


def _python_network_targets(code: str) -> list[str]:
    targets: list[str] = []
    for match in _PYTHON_URL_RE.finditer(code):
        target = match.group(0).split("://", 1)[1].split("?", 1)[0].rstrip("/),]")
        if not target or any(existing.startswith(target) for existing in targets):
            continue
        targets = [existing for existing in targets if not target.startswith(existing)]
        targets.append(_bounded_preview(target, limit=64))
    return targets[:2]


def _python_tree(code: str) -> ast.AST | None:
    try:
        return ast.parse(code)
    except (SyntaxError, ValueError):
        return None


def _python_referenced_inputs(code: str) -> list[str]:
    tree = _python_tree(code)
    if tree is None:
        return []
    names = {
        str(node.slice.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "inputs"
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
        and not _is_sensitive_key(node.slice.value)
    }
    return sorted(names)


def _python_output_fields(code: str) -> list[str]:
    tree = _python_tree(code)
    if tree is None:
        return []
    fields: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "print" or not node.args:
            continue
        value = node.args[0]
        while isinstance(value, ast.Call) and value.args:
            value = value.args[0]
        if not isinstance(value, ast.Dict):
            continue
        for key in value.keys:
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                continue
            if key.value not in fields and not _is_sensitive_key(key.value):
                fields.append(key.value)
    return fields[:4]


def _generic_tool_display(tool_name: str, args: dict[str, Any]) -> tuple[str, str]:
    safe_args = {
        str(key): value
        for key, value in args.items()
        if key not in _GENERIC_EXECUTION_KEYS and not _is_sensitive_key(key)
    }
    redacted = redact_sensitive(safe_args)
    content = " · ".join(
        f"{key}: {_generic_value_preview(value)}" for key, value in sorted(redacted.items())
    )
    return tool_name.replace("_", " "), _bounded_preview(content)


def _generic_value_preview(value: Any) -> str:
    if isinstance(value, dict):
        keys = [str(key) for key in value if not _is_sensitive_key(key)]
        return f"fields {', '.join(keys[:4])}" + (f" +{len(keys) - 4}" if len(keys) > 4 else "")
    if isinstance(value, (list, tuple)):
        items = [_bounded_preview(str(item), limit=24) for item in value[:4]]
        return ", ".join(items) + (f" +{len(value) - 4}" if len(value) > 4 else "")
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    return _bounded_preview(str(value), limit=_TOOL_VALUE_MAX_CHARS)


def tool_call_display(tool_name: str, args: dict[str, Any]) -> tuple[str, str]:
    """Return a ``(label, content)`` pair describing a planned tool call.

    Both strings are stripped of terminal controls: callers append them to a
    raw Rich line, and the tool name and args are model-supplied.
    """
    if tool_name == "github_cli":
        label, content = _github_cli_display(args)
    elif tool_name == "execute_python_code":
        label, content = _python_execution_display(args)
    elif tool_name == ActionToolName.SLASH_INVOKE:
        command = str(args.get("command", "")).strip()
        raw_args = args.get("args")
        parsed_args = [str(item).strip() for item in raw_args] if isinstance(raw_args, list) else []
        label, content = "command", " ".join([command, *parsed_args]).strip()
    else:
        simple = _SIMPLE_TOOL_LABELS.get(tool_name)
        if simple is not None:
            key_label, arg_key = simple
            label, content = key_label, str(args.get(arg_key, "")).strip()
        else:
            label, content = _generic_tool_display(tool_name, args)
    label = strip_terminal_controls(label)
    if label in _COMMAND_TOOL_LABELS:
        # A runnable command renders as a shell block: keep newlines and collapse
        # heredoc bodies to ``… (N lines)`` so a multi-line command reads as code
        # instead of a flattened wall.
        return label, format_shell_command_for_display(
            strip_terminal_controls(content, keep_whitespace=True)
        )
    return label, strip_terminal_controls(content)


class ActionRenderObserver:
    """Agent event observer that records planner turns not owned by action tools.

    Self-recording tools (``slash_invoke``, ``shell_run``, etc.) append their own
    history row; chat turns are recorded later by turn accounting when the
    assistant runs. ``skill_view`` gets a dedicated live event: the skill name
    on ``tool_start`` and an activation/failure child line on ``tool_end``.
    """

    def __init__(self, *, session: Session, console: Console, message: str) -> None:
        self.session = session
        self.console = console
        self.message = message
        self.planned_count = 0
        self._pending_skill_calls: dict[str, str] = {}
        self._last_skill_header: str = ""
        self._pending_result_tools: set[str] = set()

    def __call__(self, kind: str, data: dict[str, Any]) -> None:
        if kind == "llm_start":
            self._set_spinner_phase(SpinnerState.THINKING_PHASE)
            return
        if kind == "agent_end":
            # Safety flush: the batch drain below normally empties the buffer,
            # but a turn that ends without a clean drain still gets its log.
            flush_action_log(self.console, self.session)
            return
        if kind == "message_update":
            self._render_intermediate_message(data)
            return
        if kind == "tool_update":
            with contextlib.suppress(Exception):
                self.session.store.append_tool_update(
                    self.session.session_id,
                    tool=str(data.get("name") or "tool"),
                    update=data.get("update"),
                    tool_call_id=str(data.get("id") or "") or None,
                )
            return
        if kind == "tool_end":
            name = str(data.get("name", "")).strip()
            if name == ActionToolName.SKILL_VIEW:
                self._render_skill_end(data)
            elif _tool_event_id(data) in self._pending_result_tools:
                self._render_tool_result(data)
            self._pending_result_tools.discard(_tool_event_id(data))
            # update_plan is not painted into the transcript: the plan renders in
            # the pinned bottom overlay (``task_plan_overlay_ansi``) from session
            # state the tool committed.
            self._clear_active_action(data)
            if not self._has_active_action():
                self._set_spinner_phase(SpinnerState.EXECUTING_PHASE)
            # No per-iteration flush: the whole turn's calls are flushed once,
            # just before the reply (``ShellOutputSink.stream``), so same-kind
            # calls spanning iterations stay in one group. ``agent_end`` below is
            # the fallback for turns that end without streaming a reply.
            return
        if kind != "tool_start":
            return
        name = str(data.get("name", "")).strip()
        if not name:
            return
        self._set_spinner_phase(SpinnerState.INVOKING_TOOLS_PHASE)
        if name == ActionToolName.SKILL_VIEW:
            self._render_skill_start(data)
        elif name == ActionToolName.UPDATE_PLAN:
            pass  # no transcript preview; the plan shows in the pinned bottom overlay
        elif _is_internal_choice_command(name, data):
            pass  # private picker plumbing; the menu owns the visible interaction
        elif name == ActionToolName.SLASH_INVOKE:
            pass  # a user slash command is already echoed as the ``[N]`` row
        elif name in _SELF_RENDERING_TOOLS:
            pass  # owns its UI; a generic preview would duplicate it
        else:
            self._render_tool_invocation(name, data)
            self._pending_result_tools.add(_tool_event_id(data))
        if name not in _SKIP_PLAN_WORK_TOOLS and not _is_internal_choice_command(name, data):
            self._record_plan_work(name, data)
            self._set_active_action(name, data)
        if self.planned_count == 0 and name not in SELF_RECORDING_ACTION_TOOL_NAMES:
            self.session.record("cli_agent", self.message)
        self.planned_count += 1

    def _record_plan_work(self, name: str, data: dict[str, Any]) -> None:
        """Attribute this tool call to the current in_progress plan step."""
        from core.agent_harness.spi.task_plan import record_task_plan_work

        args = data.get("input")
        label, content = tool_call_display(name, args if isinstance(args, dict) else {})
        line = f"{label} {content}".strip() if content else label
        record_task_plan_work(self.session, line)

    def _set_spinner_phase(self, label: str) -> None:
        # Only relabel an already-running spinner; never activate one. Literal
        # slash turns suppress the spinner (turn_start skips ``start()``) and
        # never call ``stop()``, so activating it here would leave it on screen.
        spinner = get_turn_spinner()
        if spinner is not None and getattr(spinner, "streaming", False):
            spinner.set_phase(label)

    def _set_active_action(self, name: str, data: dict[str, Any]) -> None:
        """Fold the running tool into the spinner status row (same line).

        Stacked by tool-call id: the ReAct loop emits every ``tool_start``
        before executing the batch, so a single slot would show the last
        tool and clear on the first ``tool_end``. Scrollback keeps the
        settled ``⏺`` copy. Only relabels an already-running spinner
        (see ``_set_spinner_phase``).
        """
        spinner = get_turn_spinner()
        if spinner is None or not getattr(spinner, "streaming", False):
            return
        args = data.get("input")
        label, content = tool_call_display(name, args if isinstance(args, dict) else {})
        if name in _COMMAND_STREAMING_TOOLS:
            text = label  # the ``$ <command>`` line already shows the command
        else:
            text = f"{label} · {content}" if content else label
        spinner.set_active_action(text, action_id=_tool_event_id(data))

    def _clear_active_action(self, data: dict[str, Any]) -> None:
        spinner = get_turn_spinner()
        if spinner is not None:
            spinner.clear_active_action(_tool_event_id(data))

    def _has_active_action(self) -> bool:
        spinner = get_turn_spinner()
        return bool(spinner is not None and spinner.active_action)

    def _render_intermediate_message(self, data: dict[str, Any]) -> None:
        """Render the model's commentary preceding this iteration's tool calls.

        Skills instruct the agent to emit phase headers (``### [n/N] ...``)
        before each tool group; without live rendering that narration is
        dropped. The loop's final no-tool-call answer (``has_tool_calls``
        false) is skipped — the turn driver already streams it as the
        closing reply, so printing it here would duplicate it.
        """
        if not data.get("has_tool_calls"):
            return
        content = str(data.get("content", "")).strip()
        if not content:
            return
        if is_plan_diagnosis_prose(content):
            return
        self.console.print()
        # Intermediate narration is a working note: recessed body + dim ``·`` in
        # the same gutter column as ``Ω``, so it reads apart from the user row
        # and the bright final reply without floating as unmarked prose.
        # ``render_note_block`` sanitizes model text at ``_build_markdown_block``.
        render_note_block(self.console, content)

    def _render_skill_start(self, data: dict[str, Any]) -> None:
        """Print ``Skill <name>`` when the agent starts loading a skill."""
        args = data.get("input")
        raw_name = str(args.get("name", "")).strip() if isinstance(args, dict) else ""
        slug = strip_terminal_controls(raw_name.replace("_", "-").lower()) or "skill"
        self._pending_skill_calls[str(data.get("id") or "")] = slug
        self._last_skill_header = slug
        # ``Text`` renders the (model-supplied) skill name literally — never
        # through Rich markup.
        line = Text()
        line.append("Skill ", style=BOLD_SKILL)
        line.append(slug, style=str(TEXT))
        self.console.print()
        self.console.print(line)

    def _render_tool_invocation(self, name: str, data: dict[str, Any]) -> None:
        """Buffer the running tool for the grouped action log — no inline args.

        The visible section shows a concise status only (a trimmed command for
        runnable tools, the label alone for the rest); the ``key: value``
        arguments go into the Ctrl+O detail, never the dotted inline strip.
        """
        args = data.get("input")
        label, content = tool_call_display(name, args if isinstance(args, dict) else {})
        if label in _COMMAND_TOOL_LABELS:
            concise = _bounded_preview(content, limit=72) if content else ""
            detail = f"{_TOOL_CALL_MARKER} {label} · {content}" if content else f"{label}"
        else:
            concise = ""
            detail = f"{_TOOL_CALL_MARKER} {label}"
            if content:
                # Unfold the dotted argument strip into one indented line each.
                detail += "\n" + "\n".join(f"    {part}" for part in content.split(" · "))
        self.session.terminal.push_action_log(
            ActionLogEntry(call_id=_tool_event_id(data), kind=label, concise=concise, detail=detail)
        )

    def _render_tool_result(self, data: dict[str, Any]) -> None:
        """Fold the user-facing result under its buffered call (Ctrl+O detail).

        JSON blobs stay hidden — the closing reply summarizes those.
        """
        preview = _tool_result_preview(data.get("output"))
        if not preview:
            return
        rows = preview.splitlines() or [preview]
        result = "\n".join([f"  ↳ {rows[0]}", *(f"    {row}" for row in rows[1:])])
        self.session.terminal.append_action_result(_tool_event_id(data), result)
        self.session.terminal.inline_tool_results = True

    def _render_skill_end(self, data: dict[str, Any]) -> None:
        """Print the ``↳`` child line under the skill's ``tool_start`` parent.

        The next block (another call, a note, or the ``Ω`` reply) opens with
        its own blank line — do not add one here or the gap doubles.
        """
        slug = self._pending_skill_calls.pop(str(data.get("id") or ""), None)
        if slug is None:
            return
        output = data.get("output")
        activated = isinstance(output, dict) and bool(output.get("ok"))
        # Several skills loading in one batch print their headers first and
        # their results after; name the skill whenever the line would land
        # under another skill's header.
        subject = "Skill" if slug == self._last_skill_header else slug
        label = f"{subject} activated" if activated else f"{subject} failed to load"
        self.console.print(Text(f"  ↳ {label}", style=DIM))


__all__ = [
    "ActionRenderObserver",
    "tool_call_display",
]

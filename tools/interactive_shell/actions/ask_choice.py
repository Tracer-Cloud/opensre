"""Queue an interactive selection menu for a decision the user must make.

Raw-stdin pickers cannot run mid-turn: the REPL keeps a ``prompt_async()`` open
concurrently, so arrow-key reads would race it and terminal CPR replies would
leak into the input line (same constraint as the deferred pickers in
``actions/slash.py``). This tool therefore stores the question as a
:class:`~core.agent_harness.session.pending_choice.PendingUserChoice` and queues
the literal ``/choose`` command, which the loop dispatches with exclusive stdin.
The selected option label is auto-submitted as the next user message.

Pass ``questions`` (label, title, options) when several facts block the work —
one payload, then STOP. After answers arrive, call ``update_plan`` and execute.
A single decision still uses ``title`` + ``options``.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.spi.handoff import AskUserQuestion
from core.agent_harness.spi.session_state import (
    PendingUserChoice,
    session_terminal,
    set_auto_command,
)
from core.agent_harness.tools import ActionToolScope, execute_with_action_context
from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool, SideEffectLevel
from core.tool_framework.utils import object_schema, string_array_property, string_property
from infrastructure.safety.terminal_output import strip_terminal_controls

_MIN_OPTIONS = 2
_MAX_OPTIONS = 8
_MIN_QUESTIONS = 2
_MAX_QUESTIONS = 6
_MAX_ASK_ROUNDS = 2
_CHOOSE_COMMAND = "/choose"
_DEFAULT_HEADER = "Ask User"

_FALLBACK_INSTRUCTION = (
    "No interactive selection menu is available on this surface. If the choice "
    "is required for work to continue, present a short numbered list and ask "
    "the user to reply. If this was only an optional follow-up, do NOT park a "
    "numbered question — finish with one sentence of instructions."
)
_QUEUED_INSTRUCTION = (
    "The selection menu opens after this turn ends. End the turn now without a "
    "user-facing sentence; do NOT repeat the options as text or ask the user to "
    "type a number. The user's selection arrives as the next user message as the "
    "question followed by the chosen option label, verbatim. After selection, "
    "continue the original work and do not merely repeat or acknowledge the label."
)
_QUEUED_BATCH_INSTRUCTION = (
    "The Ask User menu opens after this turn ends. Before it, say in one or two "
    "sentences WHY you are asking rather than assuming, and preview the rounds "
    '(e.g. "two short rounds: the shape and available signals first, then scope '
    'and constraints"). Do NOT repeat the questions themselves as text and do '
    "NOT call update_plan yet. The user's answers arrive as the next user "
    "message. After they arrive, call update_plan then execute."
)

_QUESTION_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["label", "title", "options"],
    "properties": {
        "label": string_property(
            description=(
                "Short breadcrumb name, one or two words, e.g. 'Codebase', 'Metrics', 'Window'."
            ),
            min_length=1,
        ),
        "title": string_property(
            description="Full question shown for this step.",
            min_length=1,
        ),
        "options": string_array_property(
            description=(
                "Two to eight short option labels, recommended option first. "
                "The selected label is echoed back verbatim."
            ),
        ),
        "multi_select": {
            "type": "boolean",
            "description": (
                "When true, the shell shows checkboxes and the user may toggle "
                "several options (Space/Enter). Default false = single choice."
            ),
        },
    },
}


def _menu_available(ctx: ActionToolScope) -> bool:
    """True when the REPL can render the deferred ``/choose`` picker.

    Mirrors ``_slash_drives_interactive_picker``: gateway/headless sessions have
    no terminal facet, and non-TTY turns must not queue a picker back to a REPL
    loop that does not exist (e.g. gateway running under tmux with a TTY stdin).
    """
    if ctx.is_tty is False or session_terminal(ctx.session) is None:
        return False
    ports = ctx.slash_ports
    return ports is not None and bool(ports.tty_interactive())


def _parse_options(raw: object) -> list[str]:
    options: list[str] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return options
    for item in raw:
        text = strip_terminal_controls(str(item)).strip()
        if text and text not in seen:
            seen.add(text)
            options.append(text)
    return options


def _options_error(options: list[str]) -> str | None:
    if len(options) < _MIN_OPTIONS:
        return f"at least {_MIN_OPTIONS} distinct non-empty options are required"
    if len(options) > _MAX_OPTIONS:
        return f"at most {_MAX_OPTIONS} options are supported"
    return None


def _parse_bool(value: object, *, default: bool = False) -> bool:
    """Coerce tool args to bool without treating the string ``\"false\"`` as True."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
        return default
    return default


def _parse_questions(raw: object) -> tuple[list[AskUserQuestion] | None, str | None]:
    """Return ``(questions, error)``. Absent/empty ``raw`` yields ``([], None)``."""
    if raw is None:
        return [], None
    if not isinstance(raw, list):
        return None, "questions must be an array of {label, title, options}"
    if not raw:
        return [], None
    parsed: list[AskUserQuestion] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            return None, f"questions[{index}] must be an object"
        label = strip_terminal_controls(str(item.get("label", ""))).strip()
        title = strip_terminal_controls(str(item.get("title", ""))).strip()
        options = _parse_options(item.get("options"))
        if not label:
            return None, f"questions[{index}].label is required"
        if not title:
            return None, f"questions[{index}].title is required"
        option_error = _options_error(options)
        if option_error is not None:
            return None, f"questions[{index}]: {option_error}"
        multi_select = _parse_bool(item.get("multi_select"), default=False)
        parsed.append(
            AskUserQuestion(
                label=label,
                title=title,
                options=tuple(options),
                multi_select=multi_select,
            )
        )
    if len(parsed) > _MAX_QUESTIONS:
        return None, f"at most {_MAX_QUESTIONS} questions are supported"
    return parsed, None


def execute_ask_user_choice_tool(args: dict[str, Any], ctx: ActionToolScope) -> dict[str, Any]:
    questions, questions_error = _parse_questions(args.get("questions"))
    if questions_error is not None:
        return {"ok": False, "error": questions_error}

    title = strip_terminal_controls(str(args.get("title", ""))).strip()
    options = _parse_options(args.get("options"))

    if questions:
        if getattr(ctx.session, "ask_user_rounds", 0) >= _MAX_ASK_ROUNDS:
            return {
                "ok": False,
                "error": (
                    "Two clarification rounds already asked this workload — do "
                    "not ask a third. Write the diagnosis (facts, what the "
                    "signature tells us, a ranked hypothesis table with a "
                    "discriminator) and call update_plan now with your best "
                    "hypothesis."
                ),
            }
        if len(questions) < _MIN_QUESTIONS:
            return {
                "ok": False,
                "error": (
                    f"questions must contain at least {_MIN_QUESTIONS} items; "
                    "use title and options for a single decision"
                ),
            }
        header = title or _DEFAULT_HEADER
        pending = PendingUserChoice(
            title=header,
            options=questions[0].options,
            questions=tuple(questions),
        )
        queued = _QUEUED_BATCH_INSTRUCTION
        summary = f"Ask User menu queued: {len(questions)} questions"
    else:
        if not title:
            return {"ok": False, "error": "title is required"}
        option_error = _options_error(options)
        if option_error is not None:
            return {"ok": False, "error": option_error}
        multi_select = _parse_bool(args.get("multi_select"), default=False)
        pending = PendingUserChoice(
            title=title,
            options=tuple(options),
            multi_select=multi_select,
            note=strip_terminal_controls(str(args.get("note", ""))).strip(),
        )
        queued = _QUEUED_INSTRUCTION
        summary = f"selection menu queued: {title}"

    if not _menu_available(ctx):
        return {"ok": True, "menu": "unavailable", "instruction": _FALLBACK_INSTRUCTION}

    ctx.session.pending_user_choice = pending
    if questions:
        ctx.session.ask_user_rounds = getattr(ctx.session, "ask_user_rounds", 0) + 1
    set_auto_command(ctx.session, _CHOOSE_COMMAND)
    terminal = session_terminal(ctx.session)
    if terminal is not None:
        terminal.awaiting_handoff_answer = True
    return {
        "ok": True,
        "menu": "queued",
        "summary": summary,
        "instruction": queued,
    }


def run_ask_user_choice(
    *,
    title: str = "",
    options: list[str] | None = None,
    questions: list[dict[str, Any]] | None = None,
    multi_select: bool = False,
    note: str = "",
    context: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": title,
        "options": options or [],
        "multi_select": multi_select,
        "note": note,
    }
    if questions is not None:
        payload["questions"] = questions
    return execute_with_action_context(
        payload,
        context,
        execute_ask_user_choice_tool,
    )


ask_user_choice_tool = RegisteredTool(
    name="ask_user_choice",
    description=(
        "Ask the user to pick from a small fixed set via the interactive "
        "shell's selection menu. When several missing facts block a multi-step "
        "job, pass ALL of them in questions (label, title, options) in ONE "
        "call, then end the turn — do not drip questions and do not call "
        "update_plan until the answers arrive. A single decision uses title "
        "and options. Precede the call with one short sentence telling the "
        "user what you are about to ask and that they can type their own "
        "answer if none fit. The menu opens after the turn ends; answers "
        "arrive verbatim as the next user message. If the result says the "
        "menu is unavailable, fall back to a numbered list."
    ),
    use_cases=[
        (
            "A workflow is blocked on one required decision between a small "
            "fixed set of actions (e.g. stash vs commit vs worktree)"
        ),
        (
            "A prior selection or free-text answer is still ambiguous and the "
            "next step is itself a choice among a small fixed set — offer "
            "another menu rather than an open-ended 'tell me more' question"
        ),
        (
            "Triage is blocked on several facts the user must supply (where a "
            "service lives, how to get metrics, the time window) — one "
            "questions payload, then plan"
        ),
        "A skill instructs presenting a structured choice / dropdown to the user",
    ],
    anti_examples=[
        "Open-ended questions with no fixed option set (ask in plain text)",
        "Yes/no confirmations already covered by the execution confirmation flow",
        "Presenting information that requires no decision",
        "Optional end-of-turn follow-up on a headless, scheduled, gateway, or /goal turn",
        "One ask_user_choice call per question when several facts block the same job",
        "Calling update_plan before the Ask User answers arrive",
    ],
    input_schema=object_schema(
        properties={
            "title": string_property(
                description=(
                    "Menu header, or the question when questions is omitted. "
                    "Use 'Ask User' for a batched questions payload."
                ),
                min_length=1,
            ),
            "options": string_array_property(
                description=(
                    "Two to eight short option labels for a single decision, "
                    "recommended option first. Omit when questions is set."
                ),
            ),
            "note": string_property(
                description="Optional short explainer shown inside a single-question menu.",
            ),
            "questions": {
                "type": "array",
                "description": (
                    "Two to six blockers to ask in one Ask User wizard. Each "
                    "item is {label, title, options, multi_select?}. Prefer "
                    "this over several turns when missing facts block a plan."
                ),
                "items": _QUESTION_ITEM_SCHEMA,
            },
            "multi_select": {
                "type": "boolean",
                "description": (
                    "For a single title/options decision: when true, the shell "
                    "shows checkboxes and the user may toggle several options. "
                    "Ignored when questions is set (use per-question multi_select)."
                ),
            },
        },
        required=(),
    ),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_ask_user_choice,
    tags=("safe", "fast", "no-credentials"),
    side_effect_level=SideEffectLevel.READ_ONLY,
)


__all__ = [
    "ask_user_choice_tool",
    "execute_ask_user_choice_tool",
    "run_ask_user_choice",
]

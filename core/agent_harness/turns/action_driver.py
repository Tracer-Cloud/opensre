"""Action tool-calling turn driver (decoupled from any terminal surface).

Runs one turn through the shared :class:`core.agent.Agent` tool-calling
loop: it assembles the available agent tools (via a :class:`~core.agent_harness.ports.ToolProvider`),
drives the loop while a tool-event observer streams each tool call to the
surface, and summarizes the executed tool calls into a facts-only
:class:`~core.agent_harness.turns.turn_results.ToolCallingTurnResult`.

Accounting/analytics for the turn are the caller's concern (see
:class:`core.agent_harness.ports.TurnAccounting`); this module emits none itself.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from config.constants.skills import ONBOARDING_SKILL_NAME
from config.llm_reasoning_effort import apply_reasoning_effort
from core.agent import Agent, AgentRunResult
from core.agent.cancel import tool_resources_cancel_requested
from core.agent.goals import Goal
from core.agent_harness.accounting.self_recording_tools import SELF_RECORDING_ACTION_TOOL_NAMES
from core.agent_harness.accounting.token_accounting import tap_provider_usage
from core.agent_harness.agent_builder import AgentConfig, build_agent
from core.agent_harness.ports import (
    ConfirmFn,
    ErrorReporter,
    LlmFactory,
    OutputSink,
    SessionState,
    ToolProvider,
)
from core.agent_harness.prompts import (
    action_prompt_skill_and_context,
    build_action_system_prompt_envelope,
    build_action_user_message,
)
from core.agent_harness.session.integration_resolution import resolve_and_cache_integrations
from core.agent_harness.session.pending_choice import parse_ask_user_answers
from core.agent_harness.session.terminal_access import execute_cli_onboard_on_missing_key
from core.agent_harness.session_goal.review_input import collect_tool_evidence
from core.agent_harness.task_plan.conclusion import (
    blocked_steps_await_the_user,
    demo_entered_from_menu,
    demo_pick_stalled_on_skill_load,
    task_plan_awaits_reply,
    task_plan_blocks_conclusion,
)
from core.agent_harness.task_plan.evidence import (
    plan_advanced_this_turn,
    record_deliverable_shown,
)
from core.agent_harness.task_plan.ownership import session_answer_continues_plan
from core.agent_harness.turns.action_dedup import (
    coerce_fingerprint_quiet,
    with_duplicate_action_call_guard,
)
from core.agent_harness.turns.action_menu_end import with_menu_turn_end
from core.agent_harness.turns.conversation_recording import record_conversation_turn
from core.agent_harness.turns.display_text import (
    already_on_screen,
    cap_for_display,
    format_generic_tool_payload,
    host_rendered,
    is_outcome_report,
    looks_like_json,
    preferred_tool_response_text,
    split_output_truncation_markers,
    strip_plan_snapshots,
)
from core.agent_harness.turns.goal_review import (
    build_goal_reviewer,
    last_goal_rejection_reason,
    tap_executed_tool_names,
)
from core.agent_harness.turns.plan_hooks import with_task_plan_hooks
from core.agent_harness.turns.skill_activation import prepare_active_skill
from core.agent_harness.turns.skill_value import record_skill_value
from core.agent_harness.turns.turn_plan import TurnPlan
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from core.agent_harness.turns.turn_trace import turn_trace_state
from core.agent_harness.turns.wal_recorder import with_wal_recording
from core.agent_harness.turns.work_outcome import (
    ExecutedToolOutcome,
    tap_executed_tool_outcomes,
)
from core.events import runtime_event_callback_from_observer
from core.llm.types import AgentLLMResponse, SchemaDescribedTool, ToolCall
from core.tool import SideEffectLevel
from core.tool.execution import (
    ToolExecutionHooks,
    public_tool_input,
    summarize_tool_failures,
)
from core.tool_framework.tags import SUMMARIZE_OBSERVATION_TAG
from infrastructure.analytics.prompt_log.model_prompt import record_action_model_prompt
from infrastructure.analytics.prompt_log.recorder import PromptRecorder
from infrastructure.analytics.react_turn import run_react_agent_with_telemetry
from infrastructure.observability.trace.decisions import record_decision
from infrastructure.observability.trace.prompts import persist_turn_system_prompt
from infrastructure.observability.trace.spans import component_span
from infrastructure.text import is_data_blob

log = logging.getLogger(__name__)

# This is an emergency ceiling, not the normal workflow budget. Productive
# action turns may need many sequential calls; repeated observations are stopped
# independently by the stagnation guard below.
_MAX_TOOL_CALLING_ITERATIONS = 64
_MAX_STAGNANT_TOOL_ITERATIONS = 3
_EXECUTED_HISTORY_TYPES = {
    "slash",
    "shell",
    "alert",
    "synthetic_test",
    "implementation",
    "cli_command",
}


# Tools whose user-facing event is owned by the host UI, so the end-of-turn
# generic formatter must stay silent: repeating their summary would double-print,
# and their payload (e.g. the full skill body) is for the model only. update_plan
# renders as the pinned plan overlay, so its summary must not also print as text.
@dataclass(frozen=True)
class ActionTurnPlan:
    agent: Agent[Any]
    user_message: str
    llm: Any
    max_iterations: int
    # Replies the plan gate deferred and the sink already painted mid-turn.
    # They join ``response_text`` for history but are never streamed again.
    deferred_replies: list[str] = field(default_factory=list)
    # Active skill body and the other ephemeral context sent with the user message.
    prompt_skill: str = ""
    prompt_context: str = ""
    value_insights: set[str] = field(default_factory=set)
    # The reviewed goal of an LLM-selected turn; it remembers why it refused stop.
    goal: Goal | None = None


def _deferred_reply_presenter(
    output: OutputSink,
    deferred_replies: list[str],
    on_displayed: Callable[[str], None] | None = None,
) -> Callable[[str], bool]:
    """Paint a plan-deferred reply now (same gutter as a final reply); keep it once shown.

    Returns whether the reply reached the sink. A failed stream leaves it out of
    ``deferred_replies`` so the turn is not marked as streamed and the host's
    normal finalization still delivers the response text.
    """

    def present(text: str) -> bool:
        try:
            displayed_text = output.stream(label="OpenSRE", chunks=iter([text]))
        except Exception:  # noqa: BLE001 - presentation must never break the loop
            log.debug("deferred reply render failed; not marking it shown", exc_info=True)
            return False
        if not displayed_text:
            return False
        deferred_replies.append(text)
        if on_displayed is not None:
            on_displayed(displayed_text)
        return True

    return present


def _deliverable_shown(session: SessionState, text: str, value_insights: set[str]) -> None:
    """A plan ``deliverable`` reply reached the user: it earns that step, and may carry value."""
    record_deliverable_shown(session)
    record_skill_value(session, text, value_insights)


class _StaticToolCallLLM:
    """Deterministic one-shot LLM used for explicit non-LLM shell commands."""

    def __init__(self, tool_calls: list[ToolCall]) -> None:
        self._tool_calls = tool_calls
        self._used = False

    def tool_schemas(self, _tools: Sequence[SchemaDescribedTool]) -> list[dict[str, Any]]:
        return []

    def invoke(
        self,
        _messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = system
        _ = tools
        if self._used:
            return AgentLLMResponse(content="", tool_calls=[], raw_content=None)
        self._used = True
        return AgentLLMResponse(content="", tool_calls=self._tool_calls, raw_content=None)

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.input} for tc in tool_calls
            ],
        }

    @staticmethod
    def build_tool_result_message(
        tool_calls: list[ToolCall],
        results: list[Any],
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "content": json.dumps(
                [
                    {"id": tc.id, "name": tc.name, "result": result}
                    for tc, result in zip(tool_calls, results)
                ],
                default=str,
            ),
        }


def _response_text_from_history_entries(entries: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for item in entries:
        response_text = item.get("response_text")
        if isinstance(response_text, str) and response_text.strip():
            chunks.append(response_text.strip())
            continue
        chunks.append(_history_entry_fallback(item))
    return "\n".join(chunks)


def _history_entry_fallback(item: dict[str, Any]) -> str:
    kind = str(item.get("type", "action"))
    text = str(item.get("text", "")).strip()
    ok = bool(item.get("ok", True))
    status = "succeeded" if ok else "failed"
    if text:
        return f"{kind} {text} ({status})"
    return f"{kind} ({status})"


def _pop_turn_outcome_hint(session: SessionState) -> str:
    # Outcome hint lives on the shell terminal facet; other sessions have none.
    terminal = getattr(session, "terminal", None)
    pop_hint = getattr(terminal, "pop_turn_outcome_hint", None)
    if not callable(pop_hint):
        return ""
    hint = pop_hint()
    return hint.strip() if isinstance(hint, str) else ""


def _generic_tool_results(result: Any) -> list[tuple[ToolCall, Any]]:
    return [
        (tool_call, tool_result)
        for tool_call, tool_result in getattr(result, "tool_results", [])
        if tool_call.name not in SELF_RECORDING_ACTION_TOOL_NAMES
    ]


#: Tools whose repeat call only observes state, so a later identical call replaces the earlier one.
_OBSERVING_LEVELS = frozenset({SideEffectLevel.NONE, SideEffectLevel.READ_ONLY})


def _call_identity(tool_call: ToolCall) -> tuple[str, str]:
    """The tool and its public input, so a poll or a retry compares equal."""
    raw = tool_call.input if isinstance(tool_call.input, dict) else {}
    return tool_call.name, json.dumps(public_tool_input(raw), sort_keys=True, default=str)


def _tool_failed(tool_result: Any) -> bool:
    details = getattr(tool_result, "details", None)
    if isinstance(details, dict) and details.get("ok") is False:
        return True
    return bool(getattr(tool_result, "is_error", False))


def _reports_another_record(earlier: Any, later: Any) -> bool:
    """True when *earlier* names a record (a ``*_id`` field) that *later* does not.

    A read with no id in its input ("the most recent run") can reach a new
    record between two calls, and a failed ``ask_hosted_gateway`` can carry
    the ``prompt_id`` that recovers work the gateway already took. Either one
    is its own report, so the later call does not replace it.
    """
    earlier_details = getattr(earlier, "details", None)
    if not isinstance(earlier_details, dict):
        return False
    later_details = getattr(later, "details", None)
    if not isinstance(later_details, dict):
        later_details = {}
    return any(
        key.endswith("_id") and isinstance(value, str) and value and later_details.get(key) != value
        for key, value in earlier_details.items()
    )


def _current_generic_results(
    result: Any, tools_by_name: Mapping[str, Any] | None = None
) -> list[tuple[ToolCall, Any]]:
    """Generic results minus the ones a later identical call replaced.

    A status poll (``check_hosted_gateway`` while a gateway starts) and a failed
    call that was sent again report a state that no longer holds. The closing
    shows the latest. A mutating call that succeeded twice did two things, and
    a result that names a record the later one does not reported something
    else, so both stay.
    """
    results = _generic_tool_results(result)
    tools = tools_by_name or {}
    identities = [_call_identity(tool_call) for tool_call, _tool_result in results]
    latest = {identity: index for index, identity in enumerate(identities)}
    current: list[tuple[ToolCall, Any]] = []
    for index, (tool_call, tool_result) in enumerate(results):
        last = latest[identities[index]]
        if last != index and not _reports_another_record(tool_result, results[last][1]):
            level = getattr(tools.get(tool_call.name), "side_effect_level", None)
            if level in _OBSERVING_LEVELS or _tool_failed(tool_result):
                continue
        current.append((tool_call, tool_result))
    return current


def _stash_collapsed_tool_output(session: SessionState, text: str | None) -> None:
    """Remember a capped peek so Ctrl+O can expand it; no-op without a terminal.

    ``None`` never clears earlier peeks (same semantics as
    ``TerminalSession.stash_collapsed_tool_output``).
    """
    terminal = getattr(session, "terminal", None)
    if terminal is None:
        return
    stash = getattr(terminal, "stash_collapsed_tool_output", None)
    if callable(stash):
        stash(text)
        return
    # Minimal test doubles without the ring API: only record a non-None peek.
    if text is not None:
        terminal.collapsed_tool_output = text


def _preferred_tool_response_texts(result: Any) -> str:
    """Reply text of tools whose output is not on screen yet.

    A painted result (``rendered_in_shell``) is skipped like everywhere else in
    the transcript; otherwise its one-line summary reappears as the closing
    under the table it summarizes.
    """
    return "\n\n".join(_preferred_tool_chunks(result))


def _preferred_tool_chunks(
    result: Any, tools_by_name: Mapping[str, Any] | None = None
) -> list[str]:
    """User-facing ``response_text`` values not already painted by the tool."""
    return [
        text
        for text in (
            preferred_tool_response_text(tool_result)
            for tool_call, tool_result in _current_generic_results(result, tools_by_name)
            if not already_on_screen(tool_call, tool_result)
        )
        if text
    ]


def _message_text(message: Any) -> tuple[str, str]:
    """Role and stripped content of one runtime message."""
    if isinstance(message, dict):
        return str(message.get("role", "")), str(message.get("content", "") or "").strip()
    return str(getattr(message, "role", "")), str(getattr(message, "content", "") or "").strip()


def _latest_unshown_outcome_report(
    result: Any,
    final_text: str,
    deferred_replies: Sequence[str],
) -> str:
    """The model's outcome report, when it is not already the closing reply.

    A report written beside a tool call is not ``final_text``. The shell used
    to print that prose as a working note and then append every tool snapshot.
    """
    shown = {final_text.strip(), *(text.strip() for text in deferred_replies if text.strip())}
    latest = ""
    for message in getattr(result, "messages", ()) or ():
        role, content = _message_text(message)
        if role != "assistant" or not content or content in shown:
            continue
        if is_outcome_report(content):
            latest = content
    return latest


def _closing_tool_chunks(chunks: Sequence[str], *, include_outcome: bool) -> list[str]:
    """Drop outcome reports the closing must not repeat.

    An earlier queued snapshot loses to a later one. When the model or a
    deferred reply already delivered the report, every snapshot is dropped
    and a trailing confirmation (cleanup, a short status) stays.
    """
    outcome_at = [index for index, chunk in enumerate(chunks) if is_outcome_report(chunk)]
    if not include_outcome:
        drop = set(outcome_at)
    elif len(outcome_at) > 1:
        drop = set(outcome_at[:-1])
    else:
        drop = set()
    return [chunk for index, chunk in enumerate(chunks) if index not in drop]


#: A trailing confirmation of this many lines is kept after the shared cap.
_BRIEF_RESULT_MAX_LINES = 2


def _is_brief_result(text: str) -> bool:
    """True when *text* is a short confirmation rather than a log preview."""
    return text.count("\n") + 1 <= _BRIEF_RESULT_MAX_LINES


def _visible_closing_text(chunks: Sequence[str]) -> str:
    """One capped preview that still keeps a short trailing confirmation.

    The report leads. Other results share one cap so several logs cannot stack.
    A one- or two-line result at the end, such as the cleanup line, is placed
    after that cap so earlier lines cannot cut it off. One expand marker stays
    at the end.
    """
    outcome = ""
    others: list[str] = []
    for chunk in chunks:
        if is_outcome_report(chunk):
            outcome = chunk
        elif chunk:
            others.append(chunk)
    tail = ""
    if others and _is_brief_result(others[-1]):
        tail = others[-1]
        others = others[:-1]
    short: list[str] = []
    bulky: list[str] = []
    for chunk in others:
        if cap_for_display(chunk) == chunk:
            short.append(chunk)
        else:
            bulky.append(chunk)
    ordered = [part for part in (outcome, *short, *bulky) if part]
    preview = cap_for_display("\n".join(ordered)) if ordered else ""
    if not tail:
        return preview
    # A one-line result can still be thousands of characters. Cap it too, and
    # keep a single trailing marker when either part was folded.
    body, marker = split_output_truncation_markers(preview)
    tail_body, tail_marker = split_output_truncation_markers(cap_for_display(tail))
    text = "\n".join(part for part in (body, tail_body) if part)
    marker = tail_marker or marker
    if marker:
        return f"{text}\n{marker}" if text else marker
    return text


def _painted_results_only(result: Any) -> bool:
    """True when the turn's content came from tools that painted it themselves.

    ``update_plan`` and the menu tools carry no content of their own — the host
    draws them — so a turn that also updates its plan still counts.
    """
    painted = 0
    for tool_call, tool_result in getattr(result, "tool_results", []):
        details = getattr(tool_result, "details", None)
        if isinstance(details, dict) and details.get("rendered_in_shell") is True:
            painted += 1
        elif not host_rendered(tool_call, tool_result):
            return False
    return painted > 0


#: How many of the painted figures a closing must repeat to count as a restatement.
_RESTATED_FIGURE_COUNT = 3
_FIGURE_RE = re.compile(r"\d+(?:\.\d+)?")


def _painted_figures(result: Any) -> set[str]:
    """Numbers the painted report already put on screen, from its key results."""
    figures: set[str] = set()
    for _tool_call, tool_result in getattr(result, "tool_results", []):
        details = getattr(tool_result, "details", None)
        if not isinstance(details, dict) or details.get("rendered_in_shell") is not True:
            continue
        for row in details.get("key_results") or []:
            if isinstance(row, dict):
                figures.update(_FIGURE_RE.findall(str(row.get("value", ""))))
    return figures


def _restates_painted_figures(result: Any, final_text: str) -> bool:
    """True when the closing repeats figures the painted report already showed.

    Only the duplicate is dropped. A closing that interprets the report, warns
    about something, or offers a next step carries information the table does
    not, so it survives.
    """
    painted = _painted_figures(result)
    if not painted:
        return False
    return len(painted & set(_FIGURE_RE.findall(final_text))) >= _RESTATED_FIGURE_COUNT


def _self_recording_tools_only(result: Any) -> bool:
    """True when every executed tool already printed to the console.

    Those tools return a bare success flag to the model; any closing prose is
    invented without the command's on-screen output (e.g. claiming ``/health``
    was all-green after the report already showed failures).
    """
    names = [tool_call.name for tool_call, _tool_result in getattr(result, "tool_results", [])]
    return bool(names) and all(name in SELF_RECORDING_ACTION_TOOL_NAMES for name in names)


# Self-recording tools whose result payload carries the real command output
# back to the model (shell: stdout/stderr/exit_code; slash: the captured
# console output read back from the history row). A closing summary after these
# is grounded in observed output, unlike the bare success flags most
# self-recording tools return.
_GROUNDED_OUTPUT_TOOL_NAMES: frozenset[str] = frozenset({"shell_run", "slash_invoke"})


def _grounded_output_tools_only(result: Any) -> bool:
    """True when every tool this turn carried its real output back to the model.

    ``_self_recording_tools_only`` suppresses model closings because most
    self-recording tools hand the model a bare success flag, so closing prose
    would be invented. ``shell_run`` and ``slash_invoke`` are the exceptions —
    their tool results carry the real stdout/exit (or captured console output)
    back to the model — so their closing summary is grounded in output the model
    actually observed. That holds whether the turn ran one command or a chain:
    keeping the closing lets the agent report what a command did (result, exit,
    any skipped step) instead of ending on raw output, matching how a teammate
    would confirm the outcome.
    """
    names = [tool_call.name for tool_call, _tool_result in getattr(result, "tool_results", [])]
    return bool(names) and all(name in _GROUNDED_OUTPUT_TOOL_NAMES for name in names)


def _asks_the_user(final_text: str) -> bool:
    """True when the closing message asks the user something.

    A question ("Found 5 loops — remove all of them?") is direction-seeking,
    not a restatement of tool output, so the invented-summary hazard that
    justifies suppressing self-recording closings does not apply. Dropping it
    is worse than any paraphrase risk: the user sees dead air, and their "yes"
    has no recorded offer to resolve against on the next turn.
    """
    return final_text.rstrip().endswith("?")


def _has_quiet_shell_run(result: Any) -> bool:
    """True when any ``shell_run`` this turn opted into quiet mode.

    Quiet withholds live ``$``/stdout, so the usual self-recording assumption
    ("output is already on screen") is false. The model closing is the
    display — do not suppress it.
    """
    for tool_call, _tool_result in getattr(result, "tool_results", []):
        if getattr(tool_call, "name", None) != "shell_run":
            continue
        raw = getattr(tool_call, "input", None)
        if not isinstance(raw, dict):
            continue
        if coerce_fingerprint_quiet(public_tool_input(raw).get("quiet", False)):
            return True
    return False


def _generic_chunks(result: Any, tools_by_name: Mapping[str, Any] | None = None) -> list[str]:
    """User-facing text for each current generic tool result, in call order.

    A verify step that re-reads the same record (the same ``prompt_id``) and
    gets the same text back is shown once; independent results always show.
    """
    chunks: list[str] = []
    records_shown: set[tuple[str, str, str]] = set()
    for tool_call, tool_result in _current_generic_results(result, tools_by_name):
        formatted = format_generic_tool_payload(tool_call, tool_result)
        if not formatted:
            continue
        record = _record_id(tool_result)
        if record:
            key = (tool_call.name, record, formatted.strip())
            if key in records_shown:
                continue
            records_shown.add(key)
        chunks.append(formatted)
    return chunks


def _record_id(tool_result: Any) -> str:
    """The id of the record a tool result reports on, when it names one."""
    details = getattr(tool_result, "details", None)
    if not isinstance(details, dict):
        return ""
    value = details.get("prompt_id")
    return value.strip() if isinstance(value, str) else ""


def _response_text_from_generic_results(
    result: Any, tools_by_name: Mapping[str, Any] | None = None
) -> str:
    return "\n".join(_generic_chunks(result, tools_by_name))


def _generic_tool_result_counts(result: Any) -> tuple[int, int]:
    generic_results = _generic_tool_results(result)
    executed_count = len(generic_results)
    success_count = sum(
        1
        for _tool_call, tool_result in generic_results
        if not getattr(tool_result, "is_error", False)
    )
    return executed_count, success_count


def _should_stash_observation(
    result: Any,
    *,
    tools_by_name: dict[str, Any],
) -> bool:
    """True when a successful tool opted into observation summary via its tags."""
    for tool_call, tool_result in _generic_tool_results(result):
        if getattr(tool_result, "is_error", False):
            continue
        tool = tools_by_name.get(tool_call.name)
        tags = getattr(tool, "tags", ()) if tool is not None else ()
        if SUMMARIZE_OBSERVATION_TAG in tags:
            return True
    return False


def _turn_resolved_integrations(
    session: SessionState,
    turn_plan: TurnPlan | None,
) -> dict[str, Any]:
    """The turn's single resolved-integration view: from the plan, else resolve once.

    ``build_turn_plan`` already resolved integrations, so the plan is trusted even
    when the result is empty (``{}`` means "no integrations", not "unresolved").
    Only the direct-call path with no plan (some tests, headless without a turn)
    resolves here.
    """
    if turn_plan is not None:
        return dict(turn_plan.resolved_integrations)
    return dict(resolve_and_cache_integrations(session))


def _persist_tool_calling_error(session: SessionState, user_text: str, error_text: str) -> None:
    record_conversation_turn(session, user_text, error_text)


def _render_tool_calling_error(output: OutputSink, message: str) -> None:
    output.print()
    output.render_response_header("assistant")
    output.render_error(message)


def _stage_action_llm_failure(
    message: str,
    session: SessionState,
    *,
    client: Any | None,
    error_text: str,
) -> None:
    """Stage telemetry for an action-agent LLM failure on conversational input.

    Explicit ``!shell`` / literal ``/slash`` turns never invoke the hosted LLM
    (they run through ``_StaticToolCallLLM``), so a failure there stays a
    terminal-action outcome. For conversational input the LLM was the intended
    route, so the turn must be reported as a failed LLM call — not a terminal
    turn tagged ``no_conversational_agent``.
    """
    if _bang_shell_command(message) is not None or message.strip().startswith("/"):
        return
    from core.agent_harness.turns.orchestrator import stage_turn_error, stage_turn_llm_failure
    from core.llm_invoke_errors import ACTION_AGENT_ERROR

    stage_turn_error(session, ACTION_AGENT_ERROR, error_text)
    stage_turn_llm_failure(session, client=client)


def _bang_shell_command(message: str) -> str | None:
    # Explicit `!cmd` shell escape: a deterministic bypass for input the user
    # typed verbatim as a shell command. This is NOT natural-language intent
    # inference — do NOT copy this pattern for bare aliases, regex/keyword
    # matches, or "obvious" natural-language intents. Those must go through the
    # action-agent LLM selecting first-class AgentTools. Engineers have been
    # fired before for reintroducing regex/keyword intent shortcuts here.
    stripped = message.strip()
    if not stripped.startswith("!") or len(stripped) <= 1:
        return None
    cmd = " ".join(stripped[1:].split())
    return f"!{cmd}" if cmd else None


def _slash_tokens(stripped: str) -> tuple[str, list[str]]:
    """Split slash text into a command and arguments, keeping quoted spans whole.

    A quoted argument such as a five-field cron expression must survive as one
    token or the target command sees five stray positionals. Ordinary prose
    after a slash can contain an unbalanced apostrophe that ``shlex`` refuses,
    so fall back to a plain split rather than failing the dispatch.
    """
    try:
        parts = shlex.split(stripped, posix=True)
    except ValueError:
        parts = stripped.split()
    if not parts:
        return stripped, []
    return parts[0], parts[1:]


def _literal_slash_tool_call(message: str, agent_tools: list[Any]) -> ToolCall | None:
    """Deterministic ``slash_invoke`` for input the user typed as a literal ``/command``.

    Like the ``!cmd`` shell escape, this dispatches an *explicit, verbatim* command;
    it is NOT natural-language intent inference (free-form text such as "log me in"
    still goes through the action-agent LLM). Routing the typed command straight to
    the ``slash_invoke`` tool means slash commands keep working when the action-agent
    LLM is unavailable — e.g. a provider with no credit — so users can still run
    ``/login``, ``/onboard``, ``/model``, etc. to recover instead of deadlocking.

    Also accepts schedule affirmatives that ``expand_affirmative_follow_up``
    rewrote into a leading ``/cron add …`` (after stripping a vendor context
    prefix). Those expands are themselves literal slash text — not a separate
    static tool-call bypass — so they stay inside the repository-mandated
    action-selection path.

    Returns ``None`` (so the normal LLM path runs) when the input is not literal
    slash text or when ``slash_invoke`` is not an available tool this turn.
    """
    from infrastructure.harness_providers import strip_message_context_prefix

    _, remainder = strip_message_context_prefix(message)
    stripped = remainder.strip()
    if not stripped.startswith("/"):
        return None
    if not any(getattr(tool, "name", None) == "slash_invoke" for tool in agent_tools):
        return None
    if stripped == "/":
        command, args = "/", list[str]()
    else:
        command, args = _slash_tokens(stripped)
    return ToolCall(
        id="direct_slash_0",
        name="slash_invoke",
        input={"command": command, "args": args},
    )


def _build_action_agent(
    *,
    message: str,
    session: SessionState,
    agent_tools: list[Any],
    turn_snapshot: TurnSnapshot | None,
    resolved_integrations: dict[str, Any],
    llm_factory: LlmFactory,
    tool_hooks: ToolExecutionHooks | None,
    tool_resources: dict[str, Any],
    observer: Any,
    output: OutputSink,
) -> ActionTurnPlan:
    """Build the Agent for one action turn; return an ``ActionTurnPlan``.

    Detects the three branches — verbatim ``!shell``, literal ``/slash``
    (including Want-me-to yes expanded to ``/cron``), or
    LLM-selected — and picks a matching LLM (deterministic tool-call or hosted
    factory), system prompt, and user-message envelope. The caller only has to
    invoke ``.run()`` and shape the result.
    """
    bang_command = _bang_shell_command(message)
    slash_call = (
        None if bang_command is not None else _literal_slash_tool_call(message, agent_tools)
    )
    # Only LLM-selected turns get a goal reviewer: the verbatim `!shell` and
    # literal `/slash` paths execute exactly one explicit command by design,
    # so "did the agent reach the goal" is not a meaningful question there.
    goal: Goal | None = None
    executed_tool_names: list[str] = []
    executed_outcomes: list[ExecutedToolOutcome] = []
    deferred_replies: list[str] = []
    value_insights: set[str] = set()
    prompt_skill = ""
    prompt_context = ""

    if bang_command is not None:
        # Explicit `!` shell escape: dispatch the verbatim text as a shell_run call.
        llm: Any = _StaticToolCallLLM(
            [
                ToolCall(
                    id="direct_shell_0",
                    name="shell_run",
                    input={"command": bang_command},
                )
            ]
        )
        system = "Execute the explicit shell_run tool call."
        user_message = message
    elif slash_call is not None:
        # Explicit literal `/slash`. Dispatch through the same `slash_invoke`
        # AgentTool the LLM would otherwise pick, so typed commands keep working
        # when the action-agent LLM is unavailable.
        llm = _StaticToolCallLLM([slash_call])
        system = "Execute the explicit slash_invoke tool call."
        user_message = message
    else:
        llm = llm_factory()
        envelope = build_action_system_prompt_envelope(
            # No turn plan means no surface is known here; setup facts are
            # omitted rather than guessed (see _setup_state_for_surface).
            turn_snapshot or TurnSnapshot.from_session(message, session, surface=None)
        )
        # Cached half stays byte-identical across turns; ephemeral (conversation,
        # prior-action-facts) rides with the user message so Anthropic's system
        # cache_control breakpoint is not invalidated every turn.
        system = envelope.render_cached()
        prompt_skill, prompt_context = action_prompt_skill_and_context(envelope)
        user_message = build_action_user_message(message, prefix=envelope.render_ephemeral())
        # ReAct goal: host gates (unfinished plan) reject stop. Same-LLM
        # review is opt-in. The verifier reads executed tool names from the
        # shared list the event tap below fills, so it can stand down on
        # handoff/dispatch turns whose outcome is not reviewable at
        # conclusion time.
        # The skill active as the turn starts: the onboarding master when the
        # message answers its menu, so a child that loads and stops is caught.
        starting_skill = getattr(session, "active_skill", None)
        goal = build_goal_reviewer(
            llm,
            _goal_review_user_request(message, turn_snapshot),
            executed_tool_names,
            plan_incomplete=lambda: task_plan_blocks_conclusion(
                task_plan=getattr(session, "task_plan", None),
                plan_only=bool(getattr(session, "plan_only_until_authorized", False)),
            ),
            plan_awaits_reply=lambda: task_plan_awaits_reply(
                task_plan=getattr(session, "task_plan", None)
            ),
            plan_advanced=lambda: plan_advanced_this_turn(session),
            on_plan_deferred_reply=_deferred_reply_presenter(
                output,
                deferred_replies,
                lambda text: _deliverable_shown(session, text, value_insights),
            ),
            blocked_needs_user=lambda: blocked_steps_await_the_user(
                session, user_answered=bool(parse_ask_user_answers(message))
            ),
            skill_load_only=lambda: demo_pick_stalled_on_skill_load(
                session,
                user_answered=bool(parse_ask_user_answers(message)),
                from_onboarding_menu=starting_skill == ONBOARDING_SKILL_NAME,
                entered_from_menu=demo_entered_from_menu(starting_skill, message),
            ),
            executed_outcomes=executed_outcomes,
            trace_context=lambda: turn_trace_state(session),
        )

    # WAL first, observer second: the tool intent must be on disk before
    # any surface side effect reacts to the same event.
    on_runtime_event = with_wal_recording(
        runtime_event_callback_from_observer(observer),
        session=session,
        user_text=message,
    )
    # Every finished model call lands on ``session.tokens`` as it happens, so
    # ``/cost`` and ``/goal`` count the spend even when a later call raises.
    on_runtime_event = tap_provider_usage(on_runtime_event, session)
    if goal is not None:
        on_runtime_event = tap_executed_tool_names(on_runtime_event, executed_tool_names)
        on_runtime_event = tap_executed_tool_outcomes(on_runtime_event, executed_outcomes)

    config = AgentConfig(
        llm=llm,
        system=system,
        tools=tuple(agent_tools),
        resolved_integrations=resolved_integrations,
        max_iterations=_MAX_TOOL_CALLING_ITERATIONS,
        max_stagnant_iterations=_MAX_STAGNANT_TOOL_ITERATIONS,
        tool_resources=tool_resources,
        tool_hooks=tool_hooks,
        on_runtime_event=on_runtime_event,
        goal=goal,
    )
    return ActionTurnPlan(
        agent=build_agent(config),
        user_message=user_message,
        llm=llm,
        max_iterations=_MAX_TOOL_CALLING_ITERATIONS,
        deferred_replies=deferred_replies,
        value_insights=value_insights,
        prompt_skill=prompt_skill,
        prompt_context=prompt_context,
        goal=goal,
    )


def _goal_review_user_request(message: str, turn_snapshot: TurnSnapshot | None) -> str:
    """Recover the original user request when this turn contains Ask User answers."""
    if turn_snapshot is None or not parse_ask_user_answers(message):
        return message
    for role, content in reversed(turn_snapshot.conversation_messages):
        if role.casefold() != "user" or not content.strip():
            continue
        if parse_ask_user_answers(content):
            continue
        return content
    return message


@dataclass(frozen=True)
class _ActionTurnArgs:
    """Internal args for one ``_run_action_turn`` call."""

    output: OutputSink
    tools: ToolProvider
    llm_factory: LlmFactory
    confirm_fn: ConfirmFn | None = None
    is_tty: bool | None = None
    turn_plan: TurnPlan | None = None
    error_reporter: ErrorReporter | None = None
    tool_hooks: ToolExecutionHooks | None = None


@dataclass(frozen=True)
class ActionTurnRunner:
    """Runs action turns for one surface.

    Where output goes, which tools exist, how errors are reported and how tools
    are hooked all belong to the surface and outlive any single turn, so they are
    given once here instead of being restated on every call.

    Only ``turn_plan``, ``is_tty`` and ``confirm_fn`` change between turns, so
    they stay arguments to :meth:`run`.

    ``llm_factory`` is required: the composition root must wire a real factory
    (e.g. :func:`~core.agent_harness.llm_resolution.default_llm_factory`)
    explicitly rather than relying on a silent fallback deep in the turn
    driver. A missing factory fails here, at construction, not mid-turn.
    """

    output: OutputSink
    tools: ToolProvider
    llm_factory: LlmFactory
    error_reporter: ErrorReporter | None = None
    tool_hooks: ToolExecutionHooks | None = None

    def __post_init__(self) -> None:
        if self.llm_factory is None:
            raise ValueError(
                "No LLM provider configured for the action turn: ActionTurnRunner "
                "requires an explicit llm_factory. Wire one at the composition root "
                "(e.g. core.agent_harness.llm_resolution.default_llm_factory)."
            )

    def run(
        self,
        message: str,
        session: SessionState,
        *,
        turn_plan: TurnPlan | None = None,
        is_tty: bool | None = None,
        confirm_fn: ConfirmFn | None = None,
    ) -> ToolCallingTurnResult:
        """Run one action tool-calling turn for ``message`` against ``session``.

        ``turn_plan`` is the turn-wide assembly. Its snapshot builds the
        action-agent system prompt so the prompt reflects turn-start state rather
        than the live (potentially mid-mutation) session, and its resolved
        integrations build the action tools so prompt and tools agree.
        """
        args = _ActionTurnArgs(
            output=self.output,
            tools=self.tools,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            llm_factory=self.llm_factory,
            turn_plan=turn_plan,
            error_reporter=self.error_reporter,
            tool_hooks=self.tool_hooks,
        )
        with component_span("action_turn", session_id=getattr(session, "session_id", None)):
            return _run_action_turn(message, session, args)


@dataclass(frozen=True)
class _TurnCounts:
    """What ran this turn, counted once from history rows and tool results."""

    executed_entries: list[dict[str, Any]]
    executed_count: int
    executed_success_count: int
    generic_success_count: int
    planned_count: int
    handled: bool


def _compose_response(
    result: Any,
    session: SessionState,
    counts: _TurnCounts,
    deferred_replies: Sequence[str] = (),
    tools_by_name: Mapping[str, Any] | None = None,
) -> tuple[str, list[str], bool]:
    """Build the turn's response text and what to show on screen.

    Returns ``(response_text, display_chunks, use_final_text)``. The two differ
    on purpose: self-recording tools (shell, slash) already printed their own
    output, so the console shows only the closing text, generic tool results and
    any hint. ``response_text`` keeps the history as well, because persistence
    and non-TTY surfaces have nothing else to read. ``deferred_replies`` were
    painted mid-turn by the plan gate, so they join the history, not the screen.
    ``tools_by_name`` tells a repeated read-only poll from a repeated action.

    Consumes the session's pending outcome hint.
    """
    final_text = str(getattr(result, "final_text", "") or "").strip()
    waiting_for_choice = getattr(session, "pending_user_choice", None) is not None
    generic_text = _response_text_from_generic_results(result, tools_by_name)
    hint = _pop_turn_outcome_hint(session)
    terminal = getattr(session, "terminal", None)
    pending_choice_response = getattr(terminal, "pending_choice_response", None)
    selected_choice = (
        pending_choice_response.strip() if isinstance(pending_choice_response, str) else ""
    )
    if selected_choice and terminal is not None:
        terminal.pending_choice_response = None
    # Self-recording tools (slash/shell/…) already rendered the real output.
    # Drop model closings so they cannot contradict what the user just saw
    # (classic failure: inventing "health check passed" after a failed /health).
    # Exceptions: a shell/slash command, whose closing summary is grounded in the
    # output the model observed (so the agent can confirm the outcome, one command
    # or a chain); a closing question, which seeks direction instead of restating
    # output; and any quiet ``shell_run``, which withheld live stdout so the
    # closing *is* the turn's display.
    suppression_reason = None
    if waiting_for_choice and _is_redundant_choice_invitation(result, final_text):
        suppression_reason = "redundant_choice_invitation"
    elif _is_choice_acknowledgement(final_text, selected_choice):
        suppression_reason = "choice_acknowledgement"
    elif (
        _painted_results_only(result)
        and _restates_painted_figures(result, final_text)
        and not _asks_the_user(final_text)
    ):
        suppression_reason = "painted_figures_recap"
    elif (
        _self_recording_tools_only(result)
        and not _grounded_output_tools_only(result)
        and not _asks_the_user(final_text)
        and not _has_quiet_shell_run(result)
    ):
        suppression_reason = "self_recording_tools"
    final_text_chunk = "" if suppression_reason else final_text
    # The model sometimes restates the plan (or every historical snapshot) in its
    # reply; the pinned overlay already shows it, so strip snapshots from display.
    display_final = strip_plan_snapshots(final_text_chunk)
    # History entries are already rendered by self-recording tools (shell/slash/…).
    # Console display uses final_text + generic results + hints only so users see
    # github_cli / other registry tools without double-printing shell output.
    # response_text still includes history for persistence / non-TTY surfaces.
    assistant_report = _latest_unshown_outcome_report(result, final_text_chunk, deferred_replies)
    closing_already_has_report = is_outcome_report(final_text_chunk) or any(
        is_outcome_report(text) for text in deferred_replies
    )
    outcome_already_delivered = bool(assistant_report) or closing_already_has_report
    generic_chunks = _generic_chunks(result, tools_by_name)
    closing_chunks = _closing_tool_chunks(
        generic_chunks, include_outcome=not outcome_already_delivered
    )
    # A queued repair snapshot and the later succeeded snapshot are one report.
    # The report stays ahead of the cap: a long result before it must not hide
    # the outcome, whether or not an earlier snapshot was dropped.
    display_generic = _visible_closing_text(closing_chunks)
    # Defense: never fence a data blob into the transcript (summary/stdout leaks
    # used to pretty-print truncated JSON behind a text fence).
    if is_data_blob(generic_text):
        display_generic = ""
    # The shell observer already nested user-facing results under each ``⏺``
    # call (Droid / Claude Code / Cursor). Repeating them in the closing
    # would float a second copy after the reply. Leave the Ctrl+O stash the
    # observer wrote; do not clear it with an empty preview.
    already_inline = bool(getattr(terminal, "inline_tool_results", False))
    if already_inline and terminal is not None:
        terminal.inline_tool_results = False
        display_generic = ""
    if (
        assistant_report
        and not closing_already_has_report
        and assistant_report not in display_final
    ):
        # The shell withholds this prose from the working-note gutter so it
        # is not shown twice. A closing that is itself the report wins.
        display_final = (
            f"{assistant_report}\n\n{display_final}" if display_final else assistant_report
        )
    if not final_text and not display_generic and not display_final:
        # Tool reply text is a fallback only when the model has no closing.
        # One outcome report: a later snapshot replaces the queued one.
        # Cap it here: this path is the visible reply, and the generic-output
        # path's cap does not apply once inline results cleared that preview.
        display_final = _visible_closing_text(
            _preferred_tool_chunks(result, tools_by_name)
            if closing_chunks == generic_chunks
            else closing_chunks
        )
    is_json = looks_like_json(generic_text)
    body, markers = split_output_truncation_markers(display_generic)
    truncated = bool(markers)
    if not already_inline:
        _stash_collapsed_tool_output(session, generic_text if truncated else None)
    bulky = display_generic.count("\n") >= 4 or truncated
    if display_generic and (is_json or bulky):
        # Truncated JSON is invalid — fencing it as ``json`` makes Rich/Pygments
        # paint error tokens (red blocks) on the cut. Use a text fence instead
        # and keep truncation markers outside the block.
        if truncated:
            if body:
                display_generic = f"\n```text\n{body}\n```\n{markers}"
            else:
                display_generic = f"\n```text\n{display_generic}\n```"
        else:
            lang = "json" if is_json else "text"
            display_generic = f"\n```{lang}\n{display_generic}\n```"
    display_chunks = [chunk for chunk in (display_final, display_generic, hint) if chunk]
    history_generic = (
        "\n".join(
            _closing_tool_chunks(generic_chunks, include_outcome=not outcome_already_delivered)
        )
        if closing_chunks != generic_chunks
        else generic_text
    )
    response_chunks = [
        chunk
        for chunk in (
            _response_text_from_history_entries(counts.executed_entries),
            *deferred_replies,
            "" if closing_already_has_report else assistant_report,
            final_text_chunk,
            history_generic,
            hint,
        )
        if chunk
    ]
    # Promoting the withheld report marks the reply streamed, so a host does
    # not finalize the unfiltered tool snapshots afterwards.
    use_final_text = bool(final_text_chunk) or (
        bool(assistant_report) and not closing_already_has_report
    )
    response_text = "\n".join(response_chunks)
    record_decision(
        "response_composition",
        attributes={
            "final_text": final_text,
            "suppression_reason": suppression_reason,
            "display_text": "\n".join(display_chunks),
            "response_text": response_text,
            "inline_tool_results": already_inline,
            "plan_snapshots_removed": display_final != final_text_chunk and bool(final_text_chunk),
        },
        context=lambda: turn_trace_state(session),
    )
    return response_text, display_chunks, use_final_text


def _is_redundant_choice_invitation(result: Any, final_text: str) -> bool:
    """True when a single-choice closing repeats the title or tool summary."""
    final_tokens = _choice_invitation_tokens(final_text)
    if not final_tokens:
        return True
    for tool_call, tool_result in getattr(result, "tool_results", []):
        if tool_call.name != "ask_user_choice":
            continue
        args = public_tool_input(tool_call.input)
        if args.get("questions"):
            return False
        picker_copy = {_choice_invitation_tokens(str(args.get("title", "")))}
        details = getattr(tool_result, "details", None)
        if isinstance(details, dict):
            picker_copy.add(_choice_invitation_tokens(str(details.get("summary", ""))))
        picker_copy.discard(())
        return final_tokens in picker_copy
    return False


def _choice_invitation_tokens(text: str) -> tuple[str, ...]:
    """Normalize picker copy while allowing an optional polite prefix."""
    tokens = tuple(re.findall(r"[a-z0-9]+", text.casefold()))
    if tokens[:1] == ("please",):
        return tokens[1:]
    return tokens


def _is_choice_acknowledgement(text: str, selected_choice: str) -> bool:
    """True only for a bare restatement of the selected picker label."""
    if not text or not selected_choice:
        return False
    choice = " ".join(selected_choice.casefold().split())
    response = " ".join(text.casefold().strip().rstrip(".!?").split())
    return response in {
        choice,
        f"{choice} selected",
        f"{choice} was selected",
        f"selected {choice}",
        f"selected: {choice}",
        f"you selected {choice}",
    }


def _show_response(
    output: OutputSink,
    *,
    handled: bool,
    final_text: str,
    display_chunks: list[str],
) -> str:
    """Show the turn's answer, or leave a blank line after silent tool work.

    ``final_text`` arrives empty unless the closing message reads like a real
    reply; only then is it preferred over joined ``display_chunks``. Either way
    visible prose streams through the sink (``Ω`` gutter on the shell).
    """
    # Both branches stream through the sink so the shell paints the ``Ω`` gutter
    # (Droid / Claude Code rhythm). Bare ``print`` after a lone header left
    # agent prose unmarked and flush against Thinking chrome.
    body = final_text or ("\n".join(display_chunks) if display_chunks else "")
    if body:
        if body.strip():
            return output.stream(label="OpenSRE", chunks=iter([body]))
        if handled:
            _end_silent_tool_turn(output)
        return ""
    if handled:
        _end_silent_tool_turn(output)
    return ""


def _end_silent_tool_turn(output: OutputSink) -> None:
    """After silent tool work with nothing to show: leave a blank line."""
    output.print()


def _show_completed_plan_breakdown(output: OutputSink, session: SessionState) -> None:
    """Print the one-shot per-step work breakdown when the plan is complete.

    Not while a question to the user is queued or its answer is on its way:
    a plan ending on blocked steps is still being resolved with them.
    """
    from core.agent_harness.session.terminal_access import session_terminal
    from core.agent_harness.task_plan.work_log import take_completed_plan_breakdown

    if getattr(session, "pending_user_choice", None) is not None:
        return
    terminal = session_terminal(session)
    if terminal is not None and getattr(terminal, "awaiting_handoff_answer", False):
        return
    breakdown = take_completed_plan_breakdown(session)
    if not breakdown:
        return
    output.print()
    # Shell paints ✓ steps vs ↳ work notes in different theme colors; other
    # sinks (headless / chat) keep the plain-text checklist.
    render = getattr(output, "render_plan_breakdown", None)
    if callable(render):
        render(breakdown)
    else:
        output.print(breakdown)
    # The breakdown is the last thing a completed turn prints, so it needs the
    # same blank row below as above — otherwise it butts against the prompt.
    output.print()


def _count_turn(result: Any, session: SessionState, history_start: int) -> _TurnCounts:
    """Count what ran, from the history rows this turn added plus the results."""
    executed_entries = [
        item
        for item in session.history[history_start:]
        if item.get("type") in _EXECUTED_HISTORY_TYPES
    ]
    generic_executed_count, generic_success_count = _generic_tool_result_counts(result)
    planned_count = len(result.executed)
    return _TurnCounts(
        executed_entries=executed_entries,
        executed_count=len(executed_entries) + generic_executed_count,
        executed_success_count=(
            sum(1 for item in executed_entries if item.get("ok", True)) + generic_success_count
        ),
        generic_success_count=generic_success_count,
        planned_count=planned_count,
        handled=planned_count > 0,
    )


def _record_loop_outcome(
    recorder: PromptRecorder,
    result: AgentRunResult,
    *,
    goal: Goal | None,
    stopped_short: bool,
) -> None:
    """Tell the turn's ``$ai_generation`` why the loop stopped and what failed last.

    A loop that stopped short records its real stop reason, the goal review's
    last refusal, and the last failed tool with its sanitized error in the
    recorded error, not only the generic text the user sees.
    """
    failures = summarize_tool_failures(result.tool_results)
    recorder.set_loop_outcome(
        stop_reason=result.stop_reason,
        goal_review_reason=last_goal_rejection_reason(goal),
        last_failed_tool=failures.last_failed_tool,
        last_tool_error=failures.last_tool_error,
        blocked_tool_calls=failures.blocked_tool_calls,
        tool_error_count=failures.tool_error_count,
    )
    if stopped_short:
        recorder.set_stopped_short()


def _run_action_turn(
    message: str,
    session: SessionState,
    args: _ActionTurnArgs,
) -> ToolCallingTurnResult:
    turn_plan = args.turn_plan
    turn_snapshot = turn_plan.snapshot if turn_plan is not None else None
    # Read the turn's resolved integrations once, so the action tools and the
    # AgentConfig are built from the same view (single source, no re-resolve).
    resolved_integrations = _turn_resolved_integrations(session, turn_plan)
    history_start = len(session.history)
    # Once per turn, before any tool runs: does this answer continue the
    # open plan's own workflow (host advances it, the prompt says so)?
    plan_answer_continues = (
        turn_snapshot.plan_answer_continues
        if turn_snapshot is not None
        else session_answer_continues_plan(session, message)
    )

    prepare_active_skill(session, message)
    agent_tools = args.tools.action_tools(
        confirm_fn=args.confirm_fn,
        is_tty=args.is_tty,
        resolved_integrations=resolved_integrations,
        turn_user_message=message,
    )
    tool_resources_provider = getattr(args.tools, "tool_resources", None)
    tool_resources = tool_resources_provider() if callable(tool_resources_provider) else {}
    observer = args.tools.observer(message=message)
    log.debug(
        "action_turn start tools=%s integrations=%s",
        len(agent_tools),
        len(resolved_integrations),
    )

    built: ActionTurnPlan | None = None
    try:
        # LLM selection inside _build_action_agent is inside the try so a factory
        # raise (e.g. provider unavailable) is caught and rendered like a run-loop
        # failure. Agent construction is cheap and stays with it for a single
        # failure boundary.
        built = _build_action_agent(
            message=message,
            session=session,
            agent_tools=agent_tools,
            turn_snapshot=turn_snapshot,
            resolved_integrations=resolved_integrations,
            llm_factory=args.llm_factory,
            tool_hooks=with_menu_turn_end(
                with_task_plan_hooks(
                    with_duplicate_action_call_guard(args.tool_hooks),
                    session,
                    turn_user_message=message,
                    answer_continues=plan_answer_continues,
                ),
                session,
            ),
            tool_resources=tool_resources,
            observer=observer,
            output=args.output,
        )
        # ``/effort`` lives on the session; the model clients read it from context.
        with apply_reasoning_effort(
            turn_snapshot.reasoning_effort
            if turn_snapshot is not None
            else getattr(session, "reasoning_effort", None)
        ):
            result = run_react_agent_with_telemetry(
                built.agent,
                [{"role": "user", "content": built.user_message}],
                phase="action",
                iteration_cap=built.max_iterations,
                llm=None if isinstance(built.llm, _StaticToolCallLLM) else built.llm,
                session=session,
            )
        persist_turn_system_prompt(
            session,
            phase="action_agent",
            system_prompt=result.final_system_prompt,
        )
        record_action_model_prompt(result, skill=built.prompt_skill, context=built.prompt_context)
    except Exception as exc:
        from core.llm.shared.llm_retry import LLMCreditExhaustedError

        # Billing exhaustion is a terminal control-flow condition. Rendering it
        # as an ordinary assistant response makes one-shot callers report a
        # successful turn and exit zero even though no model work completed.
        if isinstance(exc, LLMCreditExhaustedError):
            raise
        error_text = str(exc)
        if args.error_reporter is not None:
            args.error_reporter.report(
                exc, context="core.agent_harness.action_driver", expected=True
            )
        llm_client = (
            None if built is None or isinstance(built.llm, _StaticToolCallLLM) else built.llm
        )
        _stage_action_llm_failure(
            message,
            session,
            client=llm_client,
            error_text=error_text,
        )
        from config.llm_settings import get_configured_llm_provider
        from core.agent_harness.accounting.token_accounting import resolve_provider_id

        provider = resolve_provider_id(llm_client) if llm_client is not None else None
        display_text = (
            execute_cli_onboard_on_missing_key(
                session, error_text, provider=provider or get_configured_llm_provider()
            )
            or error_text
        )
        _render_tool_calling_error(args.output, display_text)
        _persist_tool_calling_error(session, message, display_text)
        session.record("cli_agent", message, ok=False)
        return ToolCallingTurnResult(
            0,
            0,
            0,
            True,
            True,
            response_text=display_text,
            accounting_status="not_run",
            stop_reason="error",
        )

    counts = _count_turn(result, session, history_start)
    tools_by_name = {getattr(t, "name", ""): t for t in agent_tools}
    response_text, display_chunks, use_final_text = _compose_response(
        result, session, counts, built.deferred_replies, tools_by_name
    )
    cancelled = tool_resources_cancel_requested(tool_resources) or bool(
        getattr(result, "cancelled", False)
    )
    # A deferred reply already went through the sink, so the turn host must not
    # finalize ``response_text`` a second time (it would repost the report).
    response_streamed = bool((use_final_text or built.deferred_replies) and not cancelled)
    # Cancelled turns stop before the host records or finalizes the response.
    # Discovery tools that opt into ``summarize_observation`` (via tool tags)
    # return structured JSON users should not see raw. Stash only those results.
    if (
        not cancelled
        and response_text.strip()
        and counts.generic_success_count > 0
        and not session.last_command_observation
        and _should_stash_observation(
            result,
            tools_by_name=tools_by_name,
        )
    ):
        session.last_command_observation = response_text
    if not cancelled:
        displayed_text = _show_response(
            args.output,
            handled=counts.handled,
            # Stream only terminal-visible chunks. ``response_text`` may also
            # contain self-recording history for persistence/headless surfaces.
            final_text="\n".join(display_chunks) if use_final_text else "",
            display_chunks=display_chunks,
        )
        record_skill_value(session, displayed_text, built.value_insights)
        _show_completed_plan_breakdown(args.output, session)
    record_decision(
        "response_displayed",
        attributes={
            "display_text": "" if cancelled else "\n".join(display_chunks),
            "cancelled": cancelled,
        },
    )

    log.debug(
        "action_turn done planned=%s executed=%s handled=%s cancelled=%s",
        counts.planned_count,
        counts.executed_count,
        counts.handled,
        cancelled,
    )
    recorder = PromptRecorder.current()
    if recorder is not None:
        _record_loop_outcome(
            recorder,
            result,
            goal=built.goal,
            stopped_short=bool(result.hit_iteration_cap and not cancelled),
        )
    tool_evidence, evidence_success_count = (
        collect_tool_evidence(getattr(result, "tool_results", ()))
        if getattr(session, "session_goal", None) is not None
        else ("", None)
    )
    return ToolCallingTurnResult(
        counts.planned_count,
        counts.executed_count,
        counts.executed_success_count,
        False,
        False if cancelled else counts.handled,
        response_text="" if cancelled else response_text,
        response_streamed=response_streamed,
        hit_iteration_cap=bool(result.hit_iteration_cap and not cancelled),
        stop_reason=result.stop_reason,
        cancelled=cancelled,
        input_tokens=getattr(result, "input_tokens", None),
        output_tokens=getattr(result, "output_tokens", None),
        tool_evidence=tool_evidence,
        evidence_success_count=evidence_success_count,
    )


__all__ = [
    "ActionTurnPlan",
    "ActionTurnRunner",
    "SELF_RECORDING_ACTION_TOOL_NAMES",
]

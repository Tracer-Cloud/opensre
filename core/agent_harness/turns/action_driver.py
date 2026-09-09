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
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from core.agent import Agent
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
    build_action_system_prompt_envelope,
    build_action_user_message,
)
from core.agent_harness.session.integration_resolution import resolve_and_cache_integrations
from core.agent_harness.session.pending_choice import parse_ask_user_answers
from core.agent_harness.session.terminal_access import execute_cli_onboard_on_missing_key
from core.agent_harness.session_goal.review_input import collect_tool_evidence
from core.agent_harness.turns.action_dedup import (
    coerce_fingerprint_quiet,
    with_duplicate_action_call_guard,
)
from core.agent_harness.turns.action_menu_end import with_menu_turn_end
from core.agent_harness.turns.conversation_recording import record_conversation_turn
from core.agent_harness.turns.display_text import (
    cap_for_display,
    format_generic_tool_payload,
    looks_like_json,
    preferred_tool_response_text,
    split_output_truncation_markers,
    strip_plan_snapshots,
)
from core.agent_harness.turns.goal_review import (
    build_goal_reviewer,
    tap_executed_tool_names,
    task_plan_blocks_conclusion,
)
from core.agent_harness.turns.skill_scope import scope_tools_to_active_skill
from core.agent_harness.turns.turn_plan import TurnPlan
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from core.agent_harness.turns.wal_recorder import with_wal_recording
from core.events import runtime_event_callback_from_observer
from core.llm.types import AgentLLMResponse, SchemaDescribedTool, ToolCall
from core.tool.execution import ToolExecutionHooks, public_tool_input
from core.tool_framework.tags import SUMMARIZE_OBSERVATION_TAG
from infrastructure.analytics.react_turn import run_react_agent_with_telemetry
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
    texts = [
        preferred_tool_response_text(tool_result)
        for _tool_call, tool_result in _generic_tool_results(result)
    ]
    return "\n\n".join(text for text in texts if text)


def _has_preferred_tool_response_text(result: Any) -> bool:
    return any(
        bool(preferred_tool_response_text(tool_result))
        for _tool_call, tool_result in _generic_tool_results(result)
    )


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


def _response_text_from_generic_results(result: Any) -> str:
    chunks: list[str] = []
    for tool_call, tool_result in _generic_tool_results(result):
        formatted = format_generic_tool_payload(tool_call, tool_result)
        if formatted:
            chunks.append(formatted)
    return "\n".join(chunks)


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

    stage_turn_error(session, "action_agent_error", error_text)
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
        user_message = build_action_user_message(message, prefix=envelope.render_ephemeral())
        # Reviewed goal: when the agent concludes after tool work, one LLM
        # check confirms the user's request was carried out; a NOT_REACHED
        # verdict nudges the loop to continue instead of stopping short
        # (e.g. "remove the cron loops" ending after only listing them).
        # The reviewer reads executed tool names from the shared list the
        # event tap below fills, so it can stand down on handoff/dispatch
        # turns whose outcome is not reviewable at conclusion time.
        goal = build_goal_reviewer(
            llm,
            _goal_review_user_request(message, turn_snapshot),
            executed_tool_names,
            plan_incomplete=lambda: task_plan_blocks_conclusion(
                task_plan=getattr(session, "task_plan", None),
                plan_only=bool(getattr(session, "plan_only_until_authorized", False)),
            ),
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
) -> tuple[str, list[str], bool]:
    """Build the turn's response text and what to show on screen.

    Returns ``(response_text, display_chunks, use_final_text)``. The two differ
    on purpose: self-recording tools (shell, slash) already printed their own
    output, so the console shows only the closing text, generic tool results and
    any hint. ``response_text`` keeps the history as well, because persistence
    and non-TTY surfaces have nothing else to read.

    Consumes the session's pending outcome hint.
    """
    final_text = str(getattr(result, "final_text", "") or "").strip()
    waiting_for_choice = getattr(session, "pending_user_choice", None) is not None
    generic_text = _response_text_from_generic_results(result)
    hint = _pop_turn_outcome_hint(session)
    prefer_tool_response_text = _has_preferred_tool_response_text(result)
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
    suppress_final = (
        (waiting_for_choice and _is_redundant_choice_invitation(result, final_text))
        or _is_choice_acknowledgement(final_text, selected_choice)
        or prefer_tool_response_text
        or (
            _self_recording_tools_only(result)
            and not _grounded_output_tools_only(result)
            and not _asks_the_user(final_text)
            and not _has_quiet_shell_run(result)
        )
    )
    final_text_chunk = "" if suppress_final else final_text
    # The model sometimes restates the plan (or every historical snapshot) in its
    # reply; the pinned overlay already shows it, so strip snapshots from display.
    display_final = strip_plan_snapshots(final_text_chunk)
    # History entries are already rendered by self-recording tools (shell/slash/…).
    # Console display uses final_text + generic results + hints only so users see
    # github_cli / other registry tools without double-printing shell output.
    # response_text still includes history for persistence / non-TTY surfaces.
    display_generic = cap_for_display(generic_text)
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
    if prefer_tool_response_text and not display_final and not display_generic:
        # A tool that ships its own reply text (a schedule card, a report
        # summary) is the closing when the model's is dropped for it; otherwise
        # the turn ends with nothing visible after the call list.
        display_final = _preferred_tool_response_texts(result)
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
    response_chunks = [
        chunk
        for chunk in (
            _response_text_from_history_entries(counts.executed_entries),
            final_text_chunk,
            generic_text,
            hint,
        )
        if chunk
    ]
    use_final_text = bool(final_text_chunk)
    response_text = "\n".join(response_chunks)
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
) -> None:
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
            output.stream(label="OpenSRE", chunks=iter([body]))
            return
        if handled:
            _end_silent_tool_turn(output)
        return
    if handled:
        _end_silent_tool_turn(output)


def _end_silent_tool_turn(output: OutputSink) -> None:
    """After silent tool work with nothing to show: leave a blank line."""
    output.print()


def _show_completed_plan_breakdown(output: OutputSink, session: SessionState) -> None:
    """Print the one-shot per-step work breakdown when the plan is complete."""
    from core.agent_harness.task_plan.work_log import take_completed_plan_breakdown

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

    agent_tools = scope_tools_to_active_skill(
        args.tools.action_tools(
            confirm_fn=args.confirm_fn,
            is_tty=args.is_tty,
            resolved_integrations=resolved_integrations,
        ),
        session,
        message,
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
                with_duplicate_action_call_guard(args.tool_hooks), session
            ),
            tool_resources=tool_resources,
            observer=observer,
        )
        result = run_react_agent_with_telemetry(
            built.agent,
            [{"role": "user", "content": built.user_message}],
            phase="action",
            iteration_cap=built.max_iterations,
            llm=built.llm,
            session=session,
        )
        persist_turn_system_prompt(
            session,
            phase="action_agent",
            system_prompt=result.final_system_prompt,
        )
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
        from core.agent_harness.accounting.token_accounting import resolve_provider_name

        provider = resolve_provider_name(llm_client) if llm_client is not None else None
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
            0, 0, 0, True, True, response_text=display_text, accounting_status="not_run"
        )

    counts = _count_turn(result, session, history_start)
    response_text, display_chunks, use_final_text = _compose_response(result, session, counts)
    cancelled = tool_resources_cancel_requested(tool_resources) or bool(
        getattr(result, "cancelled", False)
    )
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
            tools_by_name={getattr(t, "name", ""): t for t in agent_tools},
        )
    ):
        session.last_command_observation = response_text
    if not cancelled:
        _show_response(
            args.output,
            handled=counts.handled,
            # Stream only terminal-visible chunks. ``response_text`` may also
            # contain self-recording history for persistence/headless surfaces.
            final_text="\n".join(display_chunks) if use_final_text else "",
            display_chunks=display_chunks,
        )
        _show_completed_plan_breakdown(args.output, session)

    log.debug(
        "action_turn done planned=%s executed=%s handled=%s cancelled=%s",
        counts.planned_count,
        counts.executed_count,
        counts.handled,
        cancelled,
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
        response_streamed=bool(use_final_text and not cancelled),
        hit_iteration_cap=bool(result.hit_iteration_cap and not cancelled),
        cancelled=cancelled,
        input_tokens=int(getattr(result, "input_tokens", 0) or 0),
        output_tokens=int(getattr(result, "output_tokens", 0) or 0),
        tool_evidence=tool_evidence,
        evidence_success_count=evidence_success_count,
    )


__all__ = [
    "ActionTurnPlan",
    "ActionTurnRunner",
    "SELF_RECORDING_ACTION_TOOL_NAMES",
]

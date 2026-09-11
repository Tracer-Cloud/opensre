"""ReAct goal gates for action and evidence-gather turns.

Builds a :class:`~core.agent.goals.Goal` whose ``verify`` rejects stop when a
host gate still applies (unfinished task plan, gather discovery-only). An
optional same-LLM review (``OPENSRE_REACT_GOAL_LLM_REVIEW=1``) can also reject
when the agent concludes after tools; default is off so the acting prompt
proposes done and these host gates accept or refuse. Two flavors share the
same verifier:

* :func:`build_goal_reviewer` — action turns ("remove the cron loops" must not
  stop after only listing them).
* :func:`build_gather_goal_reviewer` — evidence-gather turns (a data question
  must not stop at tool/schema discovery without executing the actual query;
  observed live: three PostHog turns in a row ended on MCP tool listings and
  never ran the count the user asked for).

When the LLM review is opted in it is conservative — a wrong ``NOT_REACHED``
makes the agent flail (observed live: duplicate async dispatches). It fails
open on any LLM error, runs at most once per turn, and is skipped entirely
when no tools ran, when the agent is asking the user a question, or when the
turn ran a tool whose outcome is not reviewable this turn.

The reviewer learns which tools ran through :func:`tap_executed_tool_names`
(action) or :func:`tap_executed_tool_calls` (gather — needs args so discovery
vs metric ``call_*_tool`` can be distinguished).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from config.constants.llm import react_goal_llm_review_enabled
from core.agent.goals import Goal, GoalObservation
from core.agent_harness.closed_llm_verdict import invoke_closed_goal_verdict
from core.agent_harness.turns.gather_discovery_budget import (
    bridge_tool_target,
    is_gather_discovery_call,
    is_live_metric_query_call,
)
from core.events import RuntimeEvent, RuntimeEventCallback, ToolExecutionEndEvent
from core.llm.types import AgentLLMClient
from infrastructure.observability.trace.decisions import record_decision

# One rejection is enough to catch a stopped-short turn; the follow-up work is
# then accepted as-is. More reviews only amplify the damage when the reviewer
# itself is wrong, because every rejection burns loop iterations on nudges.
_MAX_GOAL_REVIEWS = 1

# Tools whose presence makes the turn's goal unreviewable at conclusion time:
# async dispatches must not trigger a "not yet reached" nudge before results
# arrive. Currently empty — kept as the extension point for future async tools.
_SKIP_REVIEW_TOOL_NAMES: frozenset[str] = frozenset()

_REVIEW_SYSTEM_PROMPT = (
    "You review whether an agent completed the user's goal this turn.\n"
    "Return JSON only. Set verdict to NOT_REACHED only when the goal clearly "
    "required actions the agent did not take — e.g. the user asked to change, "
    "create, or remove something and the agent only looked it up.\n"
    "An honest report of findings, an answer to a question, or a statement "
    "that there is nothing to act on all count as GOAL_REACHED. "
    "When in doubt, set verdict to GOAL_REACHED."
)

_GOAL_SUCCESS_CRITERIA = (
    "The user's request has been fully carried out, not merely inspected or partially done."
)

_GATHER_REVIEW_SYSTEM_PROMPT = (
    "You review whether an evidence-gathering agent fetched the data needed to "
    "answer the user's question this turn.\n"
    "Return JSON only. Set verdict to NOT_REACHED when the agent stopped at "
    "discovery or setup — e.g. it only listed available tools, fetched schemas, "
    "hit taxonomy/entity errors, or described / drafted the query it would run — "
    "without executing that query and reporting the resulting data.\n"
    "A reply containing the requested data, or a clear statement that the "
    "connected data sources cannot provide it *after* a real metric query was "
    "attempted, counts as GOAL_REACHED. "
    "When in doubt, set verdict to NOT_REACHED if no metric query ran."
)

_GATHER_SUCCESS_CRITERIA = (
    "The gathered results contain the actual data needed to answer the "
    "question (or a clear finding that it is unavailable after a real query) — "
    "tool listings and schema metadata are only preparation. You cannot ask "
    "the user anything during gathering: execute the query with the available "
    "tools."
)

ExecutedToolCall = tuple[str, dict[str, Any]]


def tap_executed_tool_names(
    inner: RuntimeEventCallback | None,
    names: list[str],
) -> RuntimeEventCallback:
    """Wrap ``inner`` to record each executed tool's name into ``names``."""

    def _callback(event: RuntimeEvent) -> None:
        if isinstance(event, ToolExecutionEndEvent):
            names.append(event.tool_name)
        if inner is not None:
            inner(event)

    return _callback


def tap_executed_tool_calls(
    inner: RuntimeEventCallback | None,
    calls: list[ExecutedToolCall],
) -> RuntimeEventCallback:
    """Wrap ``inner`` to record ``(tool_name, args)`` for each executed tool."""

    def _callback(event: RuntimeEvent) -> None:
        if isinstance(event, ToolExecutionEndEvent):
            calls.append((event.tool_name, dict(event.args or {})))
        if inner is not None:
            inner(event)

    return _callback


def _format_executed_calls(calls: list[ExecutedToolCall]) -> str:
    if not calls:
        return "(none)"
    parts: list[str] = []
    for name, args in calls:
        target = bridge_tool_target(args)
        if target:
            parts.append(f"{name}(tool_name={target})")
        else:
            parts.append(name)
    return ", ".join(parts)


def _gather_ran_only_discovery(calls: list[ExecutedToolCall]) -> bool:
    """True when every executed call was MCP schema/list discovery."""
    if not calls:
        return False
    return all(is_gather_discovery_call(name, args) for name, args in calls)


def _gather_ran_metric_query(calls: list[ExecutedToolCall]) -> bool:
    """True when at least one executed call was a live metric/SQL/PromQL fetch.

    Deliberately narrower than "not discovery": a Sentry issue read or a Slack
    conversation fetch is real evidence but not a formed metric query, and the
    reviewer note must not imply one ran.
    """
    return any(is_live_metric_query_call(name, args) for name, args in calls)


_PLAN_INCOMPLETE_NUDGE = (
    "The live task plan still has unfinished steps. Keep working the "
    "in_progress step (call tools), or call ask_user_choice if a fact is "
    "missing — do not pause and idle. Mark steps completed with update_plan "
    "as you finish them. A step this runtime cannot perform is marked blocked "
    "with its blocker in explanation, never completed. End the turn only when "
    "every plan step is completed or blocked."
)


_PLAN_TOOL_NAME = "update_plan"


def plan_worked_this_turn(executed_tool_names: Sequence[str]) -> bool:
    """True when this turn touched the live plan, so unfinished steps block stop.

    ``update_plan`` is the structured signal. An active ``/goal`` alone is
    not: the latest user message may redirect, and a leftover plan must
    not pull that answer back into plan execution.
    """
    return _PLAN_TOOL_NAME in executed_tool_names


def task_plan_blocks_conclusion(
    *,
    task_plan: Any | None,
    plan_only: bool,
) -> bool:
    """True when a live execution plan still requires work this turn.

    Plan-only (user asked not to run yet) never blocks. A settled plan — every
    step completed or blocked — never blocks: a blocked step has nothing left
    to run. Otherwise the agent must keep going — stopping with ``●`` on a
    mid-plan step leaves the shell idle while the overlay still shows work.
    """
    if plan_only or task_plan is None:
        return False
    steps = getattr(task_plan, "steps", None)
    if not steps:
        return False
    settled = getattr(task_plan, "is_settled", None)
    if callable(settled):
        return not bool(settled())
    if isinstance(settled, bool):
        return not settled
    return any(getattr(item, "status", None) not in {"completed", "blocked"} for item in steps)


@dataclass
class _LLMGoalReviewer:
    """``Goal.verify`` predicate: one bounded, fail-open LLM review per turn."""

    llm: AgentLLMClient
    user_goal: str
    executed_tool_names: list[str]
    system_prompt: str = _REVIEW_SYSTEM_PROMPT
    skip_tool_names: frozenset[str] = _SKIP_REVIEW_TOOL_NAMES
    # Action turns skip review on a closing question: it seeks direction from
    # the user, and nudging would make the agent act without that answer. The
    # gather loop has no user to ask mid-pass, so its reviewer keeps going.
    skip_on_question: bool = True
    # Gather only: args-aware tap so discovery-only stops are rejected without
    # waiting for the LLM (which previously treated a draft query as reached).
    executed_tool_calls: list[ExecutedToolCall] = field(default_factory=list)
    reject_discovery_only: bool = False
    # Live plan gate: when True at conclusion, reject without spending the LLM
    # review budget (the overlay still shows unfinished work).
    plan_incomplete: Callable[[], bool] | None = None
    reviews_remaining: int = field(default=_MAX_GOAL_REVIEWS)
    trace_context: Callable[[], dict[str, Any]] | None = None

    def __call__(self, observation: GoalObservation) -> bool:
        final_text = (observation.final_text or "").strip()
        # No tools ran: the conclusion is a direct answer (or a refusal), not a
        # stopped-short action chain — the case this reviewer exists for.
        if observation.evidence_count == 0:
            return self._decision(observation, True, "no_tool_evidence")
        if self.skip_on_question and final_text.endswith("?"):
            return self._decision(observation, True, "closing_question")
        names = self.executed_tool_names
        if self.executed_tool_calls:
            names = [name for name, _ in self.executed_tool_calls]
        if any(name in self.skip_tool_names for name in names):
            return self._decision(observation, True, "unreviewable_tool")
        if (
            self.plan_incomplete is not None
            and plan_worked_this_turn(names)
            and self.plan_incomplete()
        ):
            return self._decision(observation, False, "plan_incomplete")
        if self.reject_discovery_only and _gather_ran_only_discovery(self.executed_tool_calls):
            return self._decision(observation, False, "discovery_only")
        if not react_goal_llm_review_enabled():
            return self._decision(observation, True, "llm_review_disabled")
        if self.reviews_remaining <= 0:
            return self._decision(observation, True, "review_budget_exhausted")
        self.reviews_remaining -= 1
        # Fail open on transport/parse errors — a broken reviewer must not
        # force extra ReAct iterations.
        verdict = invoke_closed_goal_verdict(
            self.llm,
            prompt=self._review_message(observation),
            system=self.system_prompt,
        )
        if verdict is None:
            return self._decision(observation, True, "llm_review_unavailable")
        return self._decision(
            observation,
            verdict != "NOT_REACHED",
            "llm_goal_not_reached" if verdict == "NOT_REACHED" else "llm_goal_reached",
        )

    def _decision(self, observation: GoalObservation, accepted: bool, reason: str) -> bool:
        record_decision(
            "goal_review",
            attributes={
                "accepted": accepted,
                "reason": reason,
                "final_text": observation.final_text,
                "iteration": observation.iteration,
                "max_iterations": observation.max_iterations,
                "evidence_count": observation.evidence_count,
                "executed_tools": self.executed_tool_names,
            },
            context=self.trace_context,
        )
        return accepted

    def _review_message(self, observation: GoalObservation) -> str:
        final_text = (observation.final_text or "").strip() or "(empty)"
        tools_line = _format_executed_calls(self.executed_tool_calls)
        if tools_line == "(none)" and self.executed_tool_names:
            tools_line = ", ".join(self.executed_tool_names)
        metric_note = ""
        if self.reject_discovery_only:
            metric_note = "\nMetric query executed: " + (
                "yes" if _gather_ran_metric_query(self.executed_tool_calls) else "no"
            )
        return (
            f"User goal: {self.user_goal}\n"
            f"Actions executed this turn: {observation.evidence_count}\n"
            f"Tools executed: {tools_line}"
            f"{metric_note}\n"
            f"Agent's closing reply:\n{final_text}"
        )


def build_goal_reviewer(
    llm: AgentLLMClient,
    user_goal: str,
    executed_tool_names: list[str],
    *,
    plan_incomplete: Callable[[], bool] | None = None,
    trace_context: Callable[[], dict[str, Any]] | None = None,
) -> Goal:
    """Build a reviewed :class:`Goal` for one action turn over ``user_goal``.

    ``executed_tool_names`` is the shared list a :func:`tap_executed_tool_names`
    wrapper fills as the turn runs; the reviewer reads it at conclusion time.

    ``plan_incomplete`` — when provided — rejects conclusions while the live
    task plan still has unfinished steps, so the shell does not go idle with
    ``Plan · n/m`` and a mid-list ``●``. It applies only to a turn that
    worked the plan (see :func:`plan_worked_this_turn`).
    """
    reviewer = _LLMGoalReviewer(
        llm=llm,
        user_goal=user_goal,
        executed_tool_names=executed_tool_names,
        plan_incomplete=plan_incomplete,
        trace_context=trace_context,
    )

    def _nudge(_observation: GoalObservation) -> str:
        if (
            plan_incomplete is not None
            and plan_worked_this_turn(executed_tool_names)
            and plan_incomplete()
        ):
            return _PLAN_INCOMPLETE_NUDGE
        return (
            f"Goal not yet met: {user_goal}. "
            f"Success criteria: {_GOAL_SUCCESS_CRITERIA}. "
            "Continue gathering evidence or taking actions until the criteria "
            "are satisfied, then conclude with a clear answer."
        )

    return Goal(
        description=user_goal,
        success_criteria=_GOAL_SUCCESS_CRITERIA,
        verify=reviewer,
        nudge=_nudge,
    )


def build_gather_goal_reviewer(
    llm: AgentLLMClient,
    user_goal: str,
    executed_tool_calls: list[ExecutedToolCall] | None = None,
) -> Goal:
    """Build a reviewed :class:`Goal` for one evidence-gather pass over ``user_goal``.

    Rejects conclusions that stopped at tool/schema discovery so the gather
    loop executes the actual query instead of returning listings the assistant
    can only apologize over. No skip-tool set (async dispatch and handoff tools
    are not on the gather surface) and no closing-question skip (there is no
    user to answer one mid-pass).

    ``executed_tool_calls`` is the shared list a :func:`tap_executed_tool_calls`
    wrapper fills; discovery-only turns are rejected deterministically before
    the LLM review (a draft query after schema thrash must not count as reached).
    """
    calls = executed_tool_calls if executed_tool_calls is not None else []
    return Goal(
        description=f"Gather the live data needed to answer: {user_goal}",
        success_criteria=_GATHER_SUCCESS_CRITERIA,
        verify=_LLMGoalReviewer(
            llm=llm,
            user_goal=user_goal,
            executed_tool_names=[],
            system_prompt=_GATHER_REVIEW_SYSTEM_PROMPT,
            skip_tool_names=frozenset(),
            skip_on_question=False,
            executed_tool_calls=calls,
            reject_discovery_only=True,
        ),
    )


__all__ = [
    "build_gather_goal_reviewer",
    "build_goal_reviewer",
    "plan_worked_this_turn",
    "tap_executed_tool_calls",
    "tap_executed_tool_names",
    "task_plan_blocks_conclusion",
]

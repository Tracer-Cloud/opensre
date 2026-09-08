"""Session goal — cross-turn continuation (distinct from ReAct Goal).

Attach via an explicit host call (:func:`attach_session_goal`) or the structured
``session_goal_set`` action tool. Do not detect goals by scanning user prose.

Checklist ticks come from the ``session_goal_complete`` tool; a cheap-model
judge decides met / not yet / impossible. Reply prose never ticks or closes.

The host loop (:mod:`core.agent_harness.session_goal.run_until`) calls ``chat``
until the goal is achieved, impossible, cleared, cancelled, or hits ``max_outer_turns``.

Related leaf modules (import them directly — this module must not import them):

* :mod:`core.agent_harness.session_goal.evaluate` — host completion + evidence gate
* :mod:`core.agent_harness.session_goal.judge` — cheap-model transcript verdict
* :mod:`core.agent_harness.session_goal.validate` — cheap-model check of new ticks
* :mod:`core.agent_harness.session_goal.progress` — progress / status-line formatting only
* :mod:`core.agent_harness.session_goal.continuation` — session-goal continuation prompts
* :mod:`core.agent_harness.session_goal.persist` — flush / restore

ReAct ``core.agent.goals.Goal`` / ``goal_review`` stay the per-turn ReAct gate.
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from infrastructure.evidence.evidence_compaction import truncate_message


class SessionGoalStatus:
    """Status names for :class:`SessionGoal`."""

    ACTIVE = "active"
    PAUSED = "paused"
    ACHIEVED = "achieved"
    IMPOSSIBLE = "impossible"
    CLEARED = "cleared"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"


class SessionGoalReason:
    """Stable host reason strings for evaluate and the judge.

    Call sites compare with ``==`` / helpers — do not invent parallel phrases.
    Never embed ``session_goal:…`` tag grammar here: progress reasons can land in
    captured reply text and must not look like progress claims.
    """

    WORKING_PREFIX = "working"
    ACHIEVED_TOOL_EVIDENCE = "achieved with tool evidence"
    ACHIEVED_GENERIC = "goal achieved"
    CHECKLIST_COMPLETE = "checklist complete"
    IMPOSSIBLE = "impossible"
    NEED_TOOL_EVIDENCE = "not yet: need successful tool work"
    WAITING_HOST_SIGNAL = "waiting for an achieved signal"
    WAITING_TOOL_EVIDENCE = "waiting for an achieved signal with tool evidence"
    WAITING_USER_CHOICE = "waiting for user choice"
    PAUSED_USER_CHOICE = "paused — waiting for your choice"
    PAUSED_NO_PROGRESS = "paused — no progress after 2 turns"
    PAUSED_SAME_VERDICT = "paused — the judge returned the same verdict twice"
    # A goal turn raised (model call rejected, provider down): the loop must not
    # spend the next turn on the same failure.
    PAUSED_TURN_FAILED = "paused — the last turn failed; fix the cause, then /goal resume"
    # Distinct from PAUSED_USER_CHOICE: user ran ``/goal pause`` (status=paused).
    PAUSED_BY_USER = "paused by you"
    BUDGET_EXHAUSTED = "session-goal turn budget exhausted"
    CANCELLED = "goal cancelled"
    CLEARED = "goal cleared"
    JUDGE_UNAVAILABLE = "judge unavailable; staying active"

    @staticmethod
    def is_working(reason: str) -> bool:
        return reason.startswith(SessionGoalReason.WORKING_PREFIX)

    @staticmethod
    def working_session_turn(turn: int, max_turns: int) -> str:
        return f"working — starting session-goal turn {turn}/{max_turns}"

    @staticmethod
    def budget_exhausted(turns_used: int, max_outer_turns: int) -> str:
        return f"{SessionGoalReason.BUDGET_EXHAUSTED} ({turns_used}/{max_outer_turns})"

    @staticmethod
    def checklist_progress(done: int, total: int, next_item: str | None = None) -> str:
        if next_item is None:
            return f"checklist {done}/{total} done"
        return f"checklist {done}/{total} done — next: {next_item}"


# Character budgets for goal text. The ellipsis arithmetic lives in
# ``truncate_message`` so no call site repeats ``limit - len("...")``.
# A reason is one line of the checklist render; a condition is persisted in
# full-ish for resume.
MAX_GOAL_REASON_CHARS = 240

# How many earlier turns a continuation is reminded of. Bounded because the
# findings ride in every subsequent prompt; the most recent are what matter.
MAX_GOAL_FINDINGS = 4
MAX_GOAL_CONDITION_CHARS = 400

# Session-goal turns a goal may run before the host stops on budget.
_DEFAULT_MAX_OUTER_TURNS = 5

# Accidental paste of the interactive-shell prompt line into user text /
# goal conditions (``[1] ❯ question`` → ``question``).
_SHELL_PROMPT_CHROME = re.compile(r"^(?:\[\d+\]\s*)?❯\s+")
# Bulleted steps on their own lines: ``- item`` / ``* item``.
_BULLET_STEPS = re.compile(r"(?:^|\n)\s*[-*]\s+(\S.+)")
# A step number: ``1. ``, ``2) ``, ``(3) `` — at a line start, after a space,
# or straight after a colon or semicolon. ``group(1)`` is the opening
# parenthesis when the marker is ``(n)``.
_STEP_NUMBER = re.compile(r"(?:^|(?<=[\s:;]))(\()?(\d+)[.)]\s+")
# Where an inline enumeration may begin: after these, ``1. …`` is a list, not prose.
_LIST_LEAD_PUNCTUATION = (":", ";")


@dataclass(slots=True)
class SessionGoal:
    """Host-scoped completion condition spanning multiple ``chat`` turns."""

    condition: str
    max_outer_turns: int = 5
    status: str = SessionGoalStatus.ACTIVE
    turns_used: int = 0
    step_count: int | None = None
    checklist: tuple[str, ...] = ()
    completed: frozenset[int] = frozenset()
    # Last host/evaluator reason shown in progress output and continuation nudges.
    last_reason: str = ""
    # What earlier turns established, oldest first. Continuations are fresh
    # ``chat`` calls and history carries prose only, so without this a later
    # turn sees only its own tools and reads their absence as an absence
    # overall — reporting completed work as never done.
    findings: tuple[str, ...] = ()
    # What the previous turn told the user, recorded whether or not a tool ran.
    # Weaker than ``findings``: not established, just already said. A turn that
    # answered from history alone left no finding, so the next turn re-derived
    # the number by another route and reported a different one with no mention
    # of the first.
    last_answer: str = ""
    # Wall-clock start for ``/goal`` duration progress (``time.time()``).
    started_at: float | None = None
    # Session token totals when the goal was attached — delta is goal spend.
    token_baseline_input: int = 0
    token_baseline_output: int = 0
    # True when attached via ``/goal set``. While ACTIVE or PAUSED, a new goal
    # must not replace it. ``GOAL_REACHED`` still requires successful tool work.
    host_owned: bool = False
    # ``turns_used`` when ``completed`` last grew. Stall detection compares
    # against this so a later plateau still pauses after two idle turns.
    last_progress_turns_used: int = 0
    # The judge's previous verdict reason, shown to the judge next time so it
    # can say whether its new verdict repeats the same blocking problem.
    last_verdict: str = ""
    # The judge said this turn's verdict repeats the previous one. Ephemeral.
    verdict_repeated: bool = False
    # Checklist indices added since the last evaluate. Ephemeral — not persisted.
    new_ticks: frozenset[int] = frozenset()
    # Successful calls this turn to the goal's own tools (``session_goal_set``,
    # ``session_goal_complete``). Bookkeeping, not evidence — subtracted before
    # the evidence gate so a tick cannot vouch for itself. Ephemeral.
    bookkeeping_calls: int = 0

    def with_status(self, status: str) -> SessionGoal:
        return replace(self, status=status)

    def record_turn(self) -> SessionGoal:
        return replace(self, turns_used=self.turns_used + 1)

    def with_completed(self, completed: frozenset[int]) -> SessionGoal:
        if completed == self.completed:
            return self
        added = completed - self.completed
        if added:
            return replace(
                self,
                completed=completed,
                last_progress_turns_used=self.turns_used,
                new_ticks=added,
            )
        return replace(self, completed=completed, new_ticks=frozenset())

    def with_bookkeeping_call(self) -> SessionGoal:
        """Count one goal-tool call so it does not pass as tool evidence."""
        return replace(self, bookkeeping_calls=self.bookkeeping_calls + 1)

    def with_verdict(self, reason: str, *, repeated: bool) -> SessionGoal:
        """Remember the judge's reason for next time, and whether it repeated the last one."""
        return replace(
            self,
            last_verdict=truncate_message(reason.strip(), MAX_GOAL_REASON_CHARS),
            verdict_repeated=repeated,
        )

    def with_tool_progress(self) -> SessionGoal:
        """Mark this turn as progress so a later no-tool plateau can still stall."""
        return replace(self, last_progress_turns_used=self.turns_used)

    def with_finding(self, finding: str) -> SessionGoal:
        """Append one turn's answer to what later turns are told."""
        text = truncate_message(finding.strip(), MAX_GOAL_REASON_CHARS)
        if not text:
            return self
        return replace(self, findings=(*self.findings, text)[-MAX_GOAL_FINDINGS:])

    def with_last_answer(self, answer: str) -> SessionGoal:
        """Record what this turn told the user, for the next turn to reconcile."""
        text = truncate_message(answer.strip(), MAX_GOAL_REASON_CHARS)
        return self if not text else replace(self, last_answer=text)

    def with_reason(self, reason: str) -> SessionGoal:
        text = truncate_message(reason.strip(), MAX_GOAL_REASON_CHARS)
        return replace(self, last_reason=text)

    @property
    def checklist_complete(self) -> bool:
        if not self.checklist:
            return False
        return all(index in self.completed for index in range(len(self.checklist)))

    @property
    def unfinished_items(self) -> tuple[tuple[int, str], ...]:
        return tuple(
            (index, item)
            for index, item in enumerate(self.checklist)
            if index not in self.completed
        )

    @property
    def next_checklist_item(self) -> tuple[int, str] | None:
        unfinished = self.unfinished_items
        return unfinished[0] if unfinished else None


def _starts_a_list(condition: str, first: re.Match[str]) -> bool:
    """True when the first marker sits where an enumeration can begin.

    ``(1)`` is unambiguous anywhere. ``1.`` / ``1)`` count only at the start of
    the condition, at a line start, or after a colon or semicolon — a ``1.``
    in the middle of a sentence is prose.
    """
    if first.group(1):
        return True
    lead = condition[: first.start()].rstrip(" \t")
    return not lead or lead.endswith("\n") or lead.endswith(_LIST_LEAD_PUNCTUATION)


def _numbered_steps(condition: str) -> tuple[str, ...]:
    """Steps numbered 1, 2, 3… in order, on one line or several; else nothing."""
    marks = list(_STEP_NUMBER.finditer(condition))
    if len(marks) < 2 or not _starts_a_list(condition, marks[0]):
        return ()
    if [int(mark.group(2)) for mark in marks] != list(range(1, len(marks) + 1)):
        return ()
    items: list[str] = []
    for position, mark in enumerate(marks):
        end = marks[position + 1].start() if position + 1 < len(marks) else len(condition)
        items.append(condition[mark.end() : end].strip().rstrip(",;").strip())
    return tuple(item for item in items if item)


def derive_session_goal_checklist(
    condition: str,
    items: Sequence[str] = (),
) -> tuple[str, ...]:
    """Checklist for a new goal: caller items, else two or more steps written into ``condition``.

    Steps are ``1. … 2. …`` (also ``1)`` / ``(1)``, inline or one per line) or
    bulleted lines. An inline ``1.`` counts only where a list can begin: the
    start of the condition, a line start, or after a colon or semicolon;
    ``(1)`` counts anywhere. A single-item checklist would only echo the
    condition, so a condition without enumerated steps gets no checklist and
    the judge alone decides.
    """
    provided = tuple(str(item).strip() for item in items if str(item).strip())
    if provided:
        return provided
    numbered = _numbered_steps(condition)
    if len(numbered) >= 2:
        return numbered
    bullets = tuple(match.group(1).strip() for match in _BULLET_STEPS.finditer(condition))
    return bullets if len(bullets) >= 2 else ()


def build_session_goal(
    condition: str,
    *,
    checklist: tuple[str, ...] = (),
    max_outer_turns: int | None = None,
) -> SessionGoal:
    """Build an active agent-attached goal from structured tool input."""
    goal_condition = truncate_message(
        strip_shell_prompt_chrome(condition),
        MAX_GOAL_CONDITION_CHARS,
    )
    clean_items = derive_session_goal_checklist(goal_condition, checklist)
    max_turns = max(1, max_outer_turns) if max_outer_turns is not None else _DEFAULT_MAX_OUTER_TURNS
    if clean_items and max_outer_turns is None:
        max_turns = max(max_turns, len(clean_items))
    return SessionGoal(
        condition=goal_condition,
        max_outer_turns=max_turns,
        status=SessionGoalStatus.ACTIVE,
        step_count=len(clean_items) or None,
        checklist=clean_items,
    )


def _session_token_totals(session: Any | None) -> tuple[int, int]:
    if session is None:
        return 0, 0
    tokens = getattr(session, "tokens", None)
    io_totals = getattr(tokens, "io_totals", None)
    if callable(io_totals):
        try:
            inp, out = io_totals()
            return max(0, int(inp)), max(0, int(out))
        except (TypeError, ValueError):
            return 0, 0
    # Duck-type stores that expose a totals dict without TokenUsage.
    totals = getattr(tokens, "totals", None)
    if not isinstance(totals, dict):
        return 0, 0
    try:
        return max(0, int(totals.get("input", 0) or 0)), max(0, int(totals.get("output", 0) or 0))
    except (TypeError, ValueError):
        return 0, 0


def mark_session_goal_started(
    goal: SessionGoal,
    *,
    now: float | None = None,
    session: Any | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> SessionGoal:
    """Stamp wall-clock start + token baselines for active-status UX."""
    if input_tokens is None or output_tokens is None:
        baseline_in, baseline_out = _session_token_totals(session)
        if input_tokens is None:
            input_tokens = baseline_in
        if output_tokens is None:
            output_tokens = baseline_out
    return replace(
        goal,
        started_at=float(time.time() if now is None else now),
        token_baseline_input=max(0, int(input_tokens)),
        token_baseline_output=max(0, int(output_tokens)),
    )


def attach_session_goal(session: Any, goal: SessionGoal) -> SessionGoal:
    """Store ``goal`` on ``session`` and return it.

    Fresh active goals get a start stamp (duration / token delta) unless the
    caller already set ``started_at`` (e.g. restore from payload). Leading
    shell prompt chrome in ``condition`` is stripped so a pasted ``[n] ❯``
    line never becomes the durable goal text. A new goal identity drops the
    session task plan so completed steps from earlier work cannot credit it.
    """
    previous = getattr(session, "session_goal", None)
    new_identity = goal.started_at is None or (
        isinstance(previous, SessionGoal) and previous.started_at != goal.started_at
    )
    cleaned = strip_shell_prompt_chrome(goal.condition)
    if cleaned != goal.condition:
        goal = replace(goal, condition=cleaned)
    if goal.started_at is None and goal.status == SessionGoalStatus.ACTIVE:
        goal = mark_session_goal_started(goal, session=session)
    session.session_goal = goal
    if new_identity:
        _discard_session_task_plan(session)
    return goal


def clear_session_goal(session: Any) -> None:
    session.session_goal = None
    _discard_session_task_plan(session)


def _discard_session_task_plan(session: Any) -> None:
    """Drop the live plan so a later goal cannot inherit completed steps."""
    if hasattr(session, "task_plan"):
        session.task_plan = None


def session_goal_elapsed_seconds(
    goal: SessionGoal,
    *,
    now: float | None = None,
) -> float | None:
    """Seconds since ``started_at``, or ``None`` when the clock was never stamped."""
    if goal.started_at is None:
        return None
    clock = time.time() if now is None else now
    return max(0.0, float(clock) - float(goal.started_at))


def session_goal_token_delta(
    goal: SessionGoal,
    *,
    session: Any | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> int:
    """Token spend since attach (input+output), floored at zero."""
    if input_tokens is None or output_tokens is None:
        cur_in, cur_out = _session_token_totals(session)
        if input_tokens is None:
            input_tokens = cur_in
        if output_tokens is None:
            output_tokens = cur_out
    delta = (int(input_tokens) - int(goal.token_baseline_input)) + (
        int(output_tokens) - int(goal.token_baseline_output)
    )
    return max(0, delta)


def session_goal_is_active(session: Any) -> bool:
    """True when the session holds an active (running) session goal."""
    goal = getattr(session, "session_goal", None)
    if goal is None:
        return False
    # ``session`` is duck-typed, so the comparison is Any-typed without this.
    return bool(goal.status == SessionGoalStatus.ACTIVE)


def session_goal_is_paused(session: Any) -> bool:
    """True when the session holds a user-paused session goal."""
    goal = getattr(session, "session_goal", None)
    if goal is None:
        return False
    return bool(goal.status == SessionGoalStatus.PAUSED)


def session_goal_is_attached(session: Any) -> bool:
    """True when a goal still owns the session (``active`` or ``paused``)."""
    goal = getattr(session, "session_goal", None)
    if goal is None:
        return False
    return bool(goal.status in (SessionGoalStatus.ACTIVE, SessionGoalStatus.PAUSED))


def strip_shell_prompt_chrome(text: str) -> str:
    """Strip leading ``[n] ❯`` prompt chrome pasted into user/goal text."""
    if not text:
        return text
    cleaned = text.strip()
    while True:
        nxt = _SHELL_PROMPT_CHROME.sub("", cleaned)
        if nxt == cleaned:
            break
        cleaned = nxt.strip()
    return cleaned


def derive_session_goal_reason(goal: SessionGoal) -> str:
    """Structured reason from goal state (no LLM).

    Used by evaluate/progress/nudge so hosts stay honest and cheap. Returns a
    :class:`SessionGoalReason` string — never tag grammar.
    """
    if goal.status == SessionGoalStatus.ACHIEVED:
        return SessionGoalReason.ACHIEVED_GENERIC
    if goal.status == SessionGoalStatus.IMPOSSIBLE:
        return goal.last_reason.strip() or SessionGoalReason.IMPOSSIBLE
    if goal.status == SessionGoalStatus.PAUSED:
        return SessionGoalReason.PAUSED_BY_USER
    if goal.status == SessionGoalStatus.BUDGET_EXHAUSTED:
        return SessionGoalReason.budget_exhausted(goal.turns_used, goal.max_outer_turns)
    if goal.status == SessionGoalStatus.CANCELLED:
        return SessionGoalReason.CANCELLED
    if goal.status == SessionGoalStatus.CLEARED:
        return SessionGoalReason.CLEARED
    if goal.checklist:
        done = len(goal.completed & frozenset(range(len(goal.checklist))))
        total = len(goal.checklist)
        nxt = goal.next_checklist_item
        if nxt is None:
            return SessionGoalReason.checklist_progress(done, total)
        _index, item = nxt
        return SessionGoalReason.checklist_progress(done, total, item)
    if goal.host_owned:
        return SessionGoalReason.WAITING_HOST_SIGNAL
    return SessionGoalReason.WAITING_TOOL_EVIDENCE


def refresh_session_goal_reason(goal: SessionGoal) -> SessionGoal:
    """Attach a fresh :func:`derive_session_goal_reason` on ``goal``."""
    return goal.with_reason(derive_session_goal_reason(goal))


__all__ = [
    "MAX_GOAL_CONDITION_CHARS",
    "MAX_GOAL_REASON_CHARS",
    "SessionGoal",
    "SessionGoalReason",
    "SessionGoalStatus",
    "attach_session_goal",
    "build_session_goal",
    "clear_session_goal",
    "derive_session_goal_checklist",
    "derive_session_goal_reason",
    "mark_session_goal_started",
    "refresh_session_goal_reason",
    "session_goal_elapsed_seconds",
    "session_goal_is_active",
    "session_goal_is_attached",
    "session_goal_is_paused",
    "session_goal_token_delta",
    "strip_shell_prompt_chrome",
]

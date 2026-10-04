"""Headless scheduled prompt runner for manual loops."""

from __future__ import annotations

import importlib
import json
import logging
import re
from collections.abc import Callable, Mapping

from config.constants.ci_repair import CI_REPAIR_REPORT_BUILDER
from core.agent_harness import AgentSession, SessionCore
from core.tool import ToolExecutionHooks
from infrastructure.scheduling.scheduler.agent_runner import AgentPayload
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_MODE_AGENT,
    LOOP_MODE_PARAM,
    LOOP_REPORT_ARGS_PARAM,
    LOOP_REPORT_PARAM,
    LOOP_SKILL_PARAM,
)
from infrastructure.scheduling.scheduler.loop_prompt import loop_skill_recipe
from infrastructure.scheduling.scheduler.previous_runs import previous_runs_block
from infrastructure.scheduling.scheduler.run_activity import (
    CARRY_NOTE_MAX_CHARS,
    compact_text,
    keep_carry_note,
)
from infrastructure.scheduling.scheduler.schedule_cancel import cancel_requested_for_payload
from infrastructure.scheduling.scheduler.types import TaskReport
from integrations.scheduled_outcomes import ScheduledOutcomes

logger = logging.getLogger(__name__)

#: Report builder name -> "module:function" producing the report text from string args.
REPORT_BUILDERS: dict[str, str] = {
    "github_ci_reliability": "integrations.github.tools.ci_analytics.loop:build_report",
    CI_REPAIR_REPORT_BUILDER: "integrations.github.tools.ci_repair_loop.supervisor:build_report",
}

#: Stored params that bind a loop to one repair target (``cron add --pr`` / ``--branch``).
_TARGET_PARAMS = ("pr_number", "branch")

#: A loop's reply may end with this line to leave a note for the loop's next run.
CARRY_NOTE_MARKER = "NOTE FOR NEXT RUN:"
#: A line starting with the marker in any case, allowing Markdown emphasis or list marks.
#: A block quote (``>``) is quoted text, never the loop's own note.
_CARRY_NOTE_LINE = re.compile(
    rf"^[ \t*_`#-]*{re.escape(CARRY_NOTE_MARKER.rstrip(':'))}[ \t*_`]*:[ \t*_`]*(?P<note>.*)$",
    re.IGNORECASE,
)

_CARRY_NOTE_INSTRUCTION = (
    "To leave yourself a note for this loop's next run, end your reply with one line "
    f"`{CARRY_NOTE_MARKER} <text>` (at most {CARRY_NOTE_MAX_CHARS} characters); the "
    "scheduler stores it with this run and removes it from the delivered reply."
)

_MANUAL_LOOP_INSTRUCTIONS = f"""Scheduled report loop.

Produce only the report body requested below.
Do not start an RCA, incident investigation, alert triage, diagnosis, or remediation workflow.
Do not mention incidents, severity, hypotheses, recommended actions, or follow-up questions
unless the report request explicitly asks for those sections.
Do not send, post, notify, or message any channel from inside this turn; the scheduler
will deliver the final report body to the configured channels after this runner returns.
Use read-only tools when data is required. For GitHub star history, call
get_github_star_history and compute "Stars Gained" from the returned daily rows.
Earlier runs, when listed, are context only: still report everything the request asks for.
{_CARRY_NOTE_INSTRUCTION}
"""

_AGENT_LOOP_INSTRUCTIONS = f"""Scheduled agent loop.

Do the work the task below names, using the tools it names.
Do not load skill_view or follow a report-only skill; the task text below is the complete instruction.
Reply only with the result in the shape the task specifies; every figure and
identifier must come from a tool result.
Write the result yourself. Never paste a policy, state, ledger, queue, or any
other file's contents as the reply, even when the task tells you to read such
a file first: read it with tools and report only what this tick did and
verified.
A tick that finds nothing eligible to act on delivers nothing: reply with only
the note line described below, or one short sentence when there is no note.
Do not send, post, notify, or message any channel from inside this turn; the
scheduler will deliver your reply to the configured channels after this
runner returns.
{_CARRY_NOTE_INSTRUCTION}
"""


def build_manual_loop_prompt(payload: AgentPayload, *, previous_runs: str = "") -> str:
    """Build the headless prompt for a manual loop payload.

    ``previous_runs`` is the loop's PREVIOUS RUNS block; it goes right before the
    stored task so the tick reads its history before the work.
    """
    prompt = str(
        payload.get("loop_prompt") or payload.get("prompt") or payload.get("description") or ""
    ).strip()
    if not prompt:
        raise RuntimeError("Manual loop prompt is empty.")

    name = str(payload.get("name") or payload.get("task_name") or "manual loop").strip()
    history = f"\n\n{previous_runs}" if previous_runs else ""
    if str(payload.get(LOOP_MODE_PARAM) or "").strip() == LOOP_MODE_AGENT:
        scope = {
            key: payload[key]
            for key in ("owner", "repo", "pr_number", "branch")
            if payload.get(key)
        }
        binding = f"\nStored repository target: {json.dumps(scope)}\n" if scope else ""
        recipe = _skill_recipe(payload)
        return (
            f"{_AGENT_LOOP_INSTRUCTIONS}\nLoop name: {name}{binding}{history}"
            f"\n\nTask:\n{prompt}{recipe}"
        )
    return f"{_MANUAL_LOOP_INSTRUCTIONS}\nLoop name: {name}{history}\n\nReport request:\n{prompt}"


def _skill_recipe(payload: AgentPayload) -> str:
    """The bound workflow card as part of the task, or "" for a loop without one.

    The host adds it because agent ticks cannot discover skills themselves; a
    card that is no longer installed fails the tick instead of running without it.
    """
    name = str(payload.get(LOOP_SKILL_PARAM) or "").strip()
    if not name:
        return ""
    skill, body = loop_skill_recipe(name)
    return f"\n\nSkill recipe ({skill}):\n{body}"


def split_carry_note(reply: str) -> tuple[str, str]:
    """Split a loop's reply into the text to deliver and its note for the next run.

    The note is the marker line and every line below it, when no blank line
    separates them from the end of the reply. A marker anywhere else (in a block
    quote, or followed by more of the report) is report content: the reply is
    delivered unchanged and the note is empty.
    """
    lines = reply.rstrip().splitlines()
    start = len(lines)
    while start > 0 and lines[start - 1].strip():
        start -= 1
        if _CARRY_NOTE_LINE.match(lines[start]):
            break
    else:
        return reply, ""
    match = _CARRY_NOTE_LINE.match(lines[start])
    first = match.group("note") if match is not None else ""
    note = compact_text(" ".join([first, *lines[start + 1 :]]), CARRY_NOTE_MAX_CHARS)
    return "\n".join(lines[:start]).strip(), note.strip(" *_`")


def report_builder(payload: AgentPayload) -> Callable[[Mapping[str, str]], str] | None:
    """The deterministic builder a loop names, or None when it runs as a model turn."""
    name = str(payload.get(LOOP_REPORT_PARAM) or "").strip()
    target = REPORT_BUILDERS.get(name)
    if target is None:
        return None
    module_path, _, attribute = target.partition(":")
    builder = getattr(importlib.import_module(module_path), attribute)
    return builder  # type: ignore[no-any-return]


def _report_args(payload: AgentPayload) -> dict[str, str]:
    raw = payload.get(LOOP_REPORT_ARGS_PARAM) or "{}"
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(parsed, dict):
        raise RuntimeError("Manual loop report arguments must be a JSON object.")
    return {str(key): str(value) for key, value in parsed.items()}


def _prepare_agent_session(session: SessionCore) -> None:
    """Run the supplied task without discovering a replacement workflow."""
    session.skill_discovery_enabled = False


def _bound_to_one_target(payload: AgentPayload) -> bool:
    """Whether the loop repairs one stored PR or branch rather than sweeping a repository."""
    return any(str(payload.get(key) or "").strip() for key in _TARGET_PARAMS)


def run_manual_prompt_loop(payload: AgentPayload) -> TaskReport:
    """Run the deterministic report builder or one model turn in the stored mode.

    A model turn reads the loop's previous runs first; the note its reply leaves
    for the next run is kept with this attempt instead of being delivered.
    """
    builder = report_builder(payload)
    if builder is not None:
        built = builder(_report_args(payload))
        return built if isinstance(built, TaskReport) else TaskReport(built)
    history = previous_runs_block(str(payload.get("task_id") or ""))
    message = build_manual_loop_prompt(payload, previous_runs=history)
    agent_mode = str(payload.get(LOOP_MODE_PARAM) or "").strip() == LOOP_MODE_AGENT
    outcomes = ScheduledOutcomes(bound_target=_bound_to_one_target(payload))
    result = AgentSession.run_headless_turn(
        message,
        prepare_session=_prepare_agent_session if agent_mode else None,
        logger=logger,
        is_tty=False,
        tool_hooks=ToolExecutionHooks(after_tool_call=outcomes.observe),
        cancel_requested=cancel_requested_for_payload(payload),
    )
    report = result.primary_response_text
    if not result.answered or not report:
        raise RuntimeError("Manual loop failed: the reasoning client did not produce a report.")
    body, note = split_carry_note(report)
    if note:
        keep_carry_note(note)
    return outcomes.report(result, agent_mode=agent_mode, text=body)


__all__ = [
    "CARRY_NOTE_MARKER",
    "REPORT_BUILDERS",
    "build_manual_loop_prompt",
    "report_builder",
    "run_manual_prompt_loop",
    "split_carry_note",
]

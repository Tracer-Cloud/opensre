"""Headless scheduled prompt runner for manual loops."""

from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Callable, Mapping

from core.agent_harness import AgentSession
from infrastructure.scheduling.scheduler.agent_runner import AgentPayload
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_REPORT_ARGS_PARAM,
    LOOP_REPORT_PARAM,
)

logger = logging.getLogger(__name__)

#: Report builder name -> "module:function" producing the report text from string args.
REPORT_BUILDERS: dict[str, str] = {
    "github_ci_reliability": "integrations.github.tools.ci_analytics.loop:build_report",
}

_MANUAL_LOOP_INSTRUCTIONS = """Scheduled report loop.

Produce only the report body requested below.
Do not start an RCA, incident investigation, alert triage, diagnosis, or remediation workflow.
Do not mention incidents, severity, hypotheses, recommended actions, or follow-up questions
unless the report request explicitly asks for those sections.
Do not send, post, notify, or message any channel from inside this turn; the scheduler
will deliver the final report body to the configured channels after this runner returns.
Use read-only tools when data is required. For GitHub star history, call
get_github_star_history and compute "Stars Gained" from the returned daily rows.
"""


def build_manual_loop_prompt(payload: AgentPayload) -> str:
    """Build the headless report prompt for a manual loop payload."""
    prompt = str(
        payload.get("loop_prompt") or payload.get("prompt") or payload.get("description") or ""
    ).strip()
    if not prompt:
        raise RuntimeError("Manual loop prompt is empty.")

    name = str(payload.get("name") or payload.get("task_name") or "manual loop").strip()
    return f"{_MANUAL_LOOP_INSTRUCTIONS}\nLoop name: {name}\n\nReport request:\n{prompt}"


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


def run_manual_prompt_loop(payload: AgentPayload) -> str:
    """Produce the loop's report: a deterministic builder when it names one, else one model turn."""
    builder = report_builder(payload)
    if builder is not None:
        return builder(_report_args(payload))
    message = build_manual_loop_prompt(payload)
    result = AgentSession.run_headless_turn(
        message,
        logger=logger,
        is_tty=False,
    )
    report = result.primary_response_text
    if not result.answered or not report:
        raise RuntimeError("Manual loop failed: the reasoning client did not produce a report.")
    return report


__all__ = [
    "REPORT_BUILDERS",
    "build_manual_loop_prompt",
    "report_builder",
    "run_manual_prompt_loop",
]

"""Shell action-agent system prompt text.

The stable base lives in ``opensre_system_prompt.md`` (loaded at import time)
so the long planner prompt is editable as data and packaged with the wheel.
That file is the OpenSRE action-planner contract (handoff vs direct answer,
compound turns, slash mapping) — not a coding-agent / Codex clone.
"""

from __future__ import annotations

from core.agent_harness.prompts.action.multi_step_policy import (
    ACTION_CONVERSATIONAL_SESSION_GOAL_RULE,
    ACTION_LOCAL_SHELL_MULTI_STEP_RULE,
)

from ..system_prompt import (
    _PROMPT_FILENAME,
    OPENSRE_SYSTEM_PROMPT,
)

# When the planner should offer scheduling, given CONTEXT setup_state.
# Skill bodies (e.g. delivering-morning-briefings) own the procedural steps.
# The same text is inlined in opensre_system_prompt.md; tests require this
# constant to remain a substring of the loaded base.
ACTION_SETUP_CAPACITY_SCHEDULE_RULE = (
    "- Read the setup-state block when present: if Integrations connected are "
    "not none and this turn finished a naturally recurring skill (or the user "
    "asked for recurring work), call propose_scheduled_delivery then WAIT — "
    "do not skip the offer only because schedule_count is already > 0 unless "
    "they declined or asked for a one-off only. If Integrations connected are "
    "none, do not invent a delivery channel; hand off or route to "
    "/integrations setup.\n"
)

_SYSTEM_PROMPT_BASE = OPENSRE_SYSTEM_PROMPT

__all__ = (
    "ACTION_CONVERSATIONAL_SESSION_GOAL_RULE",
    "ACTION_LOCAL_SHELL_MULTI_STEP_RULE",
    "ACTION_SETUP_CAPACITY_SCHEDULE_RULE",
    "_PROMPT_FILENAME",
    "_SYSTEM_PROMPT_BASE",
)

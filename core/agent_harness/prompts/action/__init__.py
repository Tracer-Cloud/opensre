"""Tool-calling / slash action-agent system and user prompt assembly."""

from __future__ import annotations

from core.agent_harness.prompts.action.assemble import (
    action_prompt_skill_and_context,
    build_action_system_prompt,
    build_action_system_prompt_envelope,
    build_action_user_message,
    connected_integrations_block,
    prior_action_facts_block,
    recent_conversation_block,
    repository_context_block,
    sanitize_action_text,
)
from core.agent_harness.prompts.action.text import (
    _SYSTEM_PROMPT_BASE,
    ACTION_SETUP_CAPACITY_SCHEDULE_RULE,
)

__all__ = [
    "ACTION_SETUP_CAPACITY_SCHEDULE_RULE",
    "_SYSTEM_PROMPT_BASE",
    "action_prompt_skill_and_context",
    "build_action_system_prompt",
    "build_action_system_prompt_envelope",
    "build_action_user_message",
    "connected_integrations_block",
    "prior_action_facts_block",
    "recent_conversation_block",
    "repository_context_block",
    "sanitize_action_text",
]

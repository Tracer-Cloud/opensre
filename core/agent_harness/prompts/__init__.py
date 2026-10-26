"""Prompt builders for the decoupled agentic turn engine.

Subpackages (by agent path):

* ``kernel/`` — shared types: ``PromptEnvelope``, tiers, ``SurfaceProfile``
  (no agent-path knowledge)
* ``grounding/`` — ``DefaultPromptContextProvider``, which names a session's
  surface; distinct from harness ``grounding/``
* ``action/`` — the single tool-calling agent's system + user prompts
* ``memory/`` — the text-history fallback blocks and affirmative follow-up
  expansion
* ``runtime_facts/`` — runtime-metadata fact lines for prompt assembly
* ``skills/`` — progressive skill index (thin) + markdown bodies on demand

Root modules: ``rules.py`` (shared rule fragments), ``system_prompt.py``
(the shared ``opensre_system_prompt.md`` loader).
"""

from __future__ import annotations

from core.agent_harness.prompts.action import (
    _SYSTEM_PROMPT_BASE,
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
from core.agent_harness.prompts.kernel import (
    PromptBlock,
    PromptBlockId,
    PromptBlockKind,
    PromptEnvelope,
    PromptSurface,
    PromptTier,
    SurfaceProfile,
    profile_for,
)
from core.agent_harness.prompts.skills import (
    SKILLS_HEADER,
    list_action_skills,
    load_skill_body,
    load_skills_index,
    skills_dir,
)

__all__ = [
    "SKILLS_HEADER",
    "_SYSTEM_PROMPT_BASE",
    "PromptBlock",
    "PromptBlockId",
    "PromptBlockKind",
    "PromptEnvelope",
    "PromptSurface",
    "PromptTier",
    "SurfaceProfile",
    "action_prompt_skill_and_context",
    "build_action_system_prompt",
    "profile_for",
    "build_action_system_prompt_envelope",
    "build_action_user_message",
    "connected_integrations_block",
    "list_action_skills",
    "load_skill_body",
    "load_skills_index",
    "prior_action_facts_block",
    "recent_conversation_block",
    "repository_context_block",
    "sanitize_action_text",
    "skills_dir",
]

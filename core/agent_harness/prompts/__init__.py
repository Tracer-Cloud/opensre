"""Prompt builders for the decoupled agentic turn engine.

Subpackages (by agent path):

* ``kernel/`` — shared types: ``PromptEnvelope``, tiers, ``SurfaceProfile``
  (no agent-path knowledge)
* ``grounding/`` — prompt-side grounding providers
  (``DefaultPromptContextProvider``); distinct from harness ``grounding/``
* ``action/`` — the single tool-calling agent's system + user prompts
* ``memory/`` — conversation window + prior-turn recall fragments
* ``runtime_facts/`` — runtime-metadata fact lines for prompt assembly
* ``skills/`` — progressive skill index (thin) + markdown bodies on demand

Root modules: ``rules.py`` (shared rule fragments), ``system_prompt.py``
(the shared ``opensre_system_prompt.md`` loader).
"""

from __future__ import annotations

from core.agent_harness.prompts.action import (
    _SYSTEM_PROMPT_BASE,
    build_action_system_prompt,
    build_action_system_prompt_envelope,
    build_action_user_message,
    connected_integrations_block,
    prior_action_facts_block,
    recent_conversation_block,
    repository_context_block,
    sanitize_action_text,
)
from core.agent_harness.prompts.grounding import build_environment_block
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
    "build_action_system_prompt",
    "profile_for",
    "build_action_system_prompt_envelope",
    "build_action_user_message",
    "build_environment_block",
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

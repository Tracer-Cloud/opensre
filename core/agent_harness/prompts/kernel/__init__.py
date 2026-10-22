"""Shared prompt types: envelope, tiers, and surface Strategy — no agent-path knowledge."""

from __future__ import annotations

from core.agent_harness.prompts.kernel.envelope import (
    PromptBlock,
    PromptBlockId,
    PromptBlockKind,
    PromptEnvelope,
    PromptLayers,
    PromptTier,
)
from core.agent_harness.prompts.kernel.surfaces import (
    PromptSurface,
    SurfaceProfile,
    profile_for,
)

__all__ = [
    "PromptBlock",
    "PromptBlockId",
    "PromptBlockKind",
    "PromptEnvelope",
    "PromptLayers",
    "PromptSurface",
    "PromptTier",
    "SurfaceProfile",
    "profile_for",
]

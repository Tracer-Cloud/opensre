"""Prompt recording shared by agent hosts."""

from infrastructure.analytics.prompt_log.lifecycle import record_prompt_turn
from infrastructure.analytics.prompt_log.recorder import PromptRecorder, current_recorder

__all__ = ["PromptRecorder", "current_recorder", "record_prompt_turn"]

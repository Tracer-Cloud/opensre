"""Prompt logging sinks."""

from infrastructure.analytics.prompt_log.sinks.local_jsonl import (
    append_prompt_log_record,
)
from infrastructure.analytics.prompt_log.sinks.posthog_ai import capture_ai_generation

__all__ = ["append_prompt_log_record", "capture_ai_generation"]

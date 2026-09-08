"""Shared parameter keys for scheduled loop tasks."""

from __future__ import annotations

LOOP_CHANNELS_PARAM = "loop_channels"
LOOP_CREATED_BY_PARAM = "loop_created_by"
LOOP_DESCRIPTION_PARAM = "loop_description"
LOOP_GROUP_ID_PARAM = "loop_group_id"
LOOP_PROMPT_PARAM = "loop_prompt"
LOOP_REPORT_PARAM = "loop_report"
"""Name of a deterministic report builder that replaces the model turn for this loop."""

LOOP_REPORT_ARGS_PARAM = "loop_report_args"
"""JSON object of string arguments handed to the report builder."""

LOOP_SLACK_CHAT_ID_PARAM = "slack_chat_id"
LOOP_SLUG_PARAM = "loop_slug"
LOOP_SOURCE_PARAM = "loop_source"
LOOP_TELEGRAM_CHAT_ID_PARAM = "telegram_chat_id"
LOOP_TIME_PARAM = "loop_time"

__all__ = [
    "LOOP_CHANNELS_PARAM",
    "LOOP_CREATED_BY_PARAM",
    "LOOP_DESCRIPTION_PARAM",
    "LOOP_GROUP_ID_PARAM",
    "LOOP_PROMPT_PARAM",
    "LOOP_REPORT_ARGS_PARAM",
    "LOOP_REPORT_PARAM",
    "LOOP_SLACK_CHAT_ID_PARAM",
    "LOOP_SLUG_PARAM",
    "LOOP_SOURCE_PARAM",
    "LOOP_TELEGRAM_CHAT_ID_PARAM",
    "LOOP_TIME_PARAM",
]

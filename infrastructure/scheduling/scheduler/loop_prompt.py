"""What a manual loop runs and shows: its shipped template's text, else its stored copy."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from core.agent_harness import LoopTemplate, load_loop_template
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_DESCRIPTION_PARAM,
    LOOP_PROMPT_PARAM,
    LOOP_TEMPLATE_PARAM,
)

logger = logging.getLogger(__name__)


def _shipped_template(params: Mapping[str, str]) -> LoopTemplate | None:
    template = str(params.get(LOOP_TEMPLATE_PARAM, "")).strip()
    if not template:
        return None
    try:
        return load_loop_template(template)
    except KeyError:
        logger.warning("Loop template %r is not shipped; using the stored copy.", template)
        return None


def current_loop_prompt(params: Mapping[str, str]) -> str:
    """Return the text a tick runs; a template no longer shipped falls back to the stored copy."""
    shipped = _shipped_template(params)
    return shipped.prompt if shipped else str(params.get(LOOP_PROMPT_PARAM, "")).strip()


def current_loop_description(params: Mapping[str, str]) -> str:
    """Return the operator's description, else the shipped template's."""
    stored = str(params.get(LOOP_DESCRIPTION_PARAM, "")).strip()
    if stored:
        return stored
    shipped = _shipped_template(params)
    return shipped.description if shipped else ""


__all__ = ["current_loop_description", "current_loop_prompt"]

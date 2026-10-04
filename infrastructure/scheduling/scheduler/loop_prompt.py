"""The instructions a manual loop runs: its shipped template's text, else its stored prompt."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from core.agent_harness import load_loop_template
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_PROMPT_PARAM,
    LOOP_TEMPLATE_PARAM,
)

logger = logging.getLogger(__name__)


def current_loop_prompt(params: Mapping[str, str]) -> str:
    """Return the text a tick runs; a template no longer shipped falls back to the stored copy."""
    template = str(params.get(LOOP_TEMPLATE_PARAM, "")).strip()
    if template:
        try:
            return load_loop_template(template).prompt
        except KeyError:
            logger.warning("Loop template %r is not shipped; running the stored prompt.", template)
    return str(params.get(LOOP_PROMPT_PARAM, "")).strip()


__all__ = ["current_loop_prompt"]

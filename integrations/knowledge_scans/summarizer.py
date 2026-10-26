"""The model a scan uses to describe what it read, on the cheap tier."""

from __future__ import annotations

import logging
from typing import Any

from core.domain.knowledge import Summarize

logger = logging.getLogger(__name__)


def llm_summarizer() -> Summarize | None:
    """A prompt → text callable on the cheap tier, or ``None`` when no LLM is configured."""
    try:
        from core.llm.factory import LLMRole, get_llm

        # The same cheap tier memory extraction uses.
        llm: Any = get_llm(LLMRole.CLASSIFICATION)
    except Exception:  # noqa: BLE001 - a scan without a model still writes its facts
        logger.debug("[knowledge] scan LLM unavailable", exc_info=True)
        return None

    def summarize(prompt: str) -> str:
        result = llm.invoke(prompt)
        content = getattr(result, "content", result)
        return content if isinstance(content, str) else str(content)

    return summarize


__all__ = ["llm_summarizer"]

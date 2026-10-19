"""The model a scan uses to describe what it read, on the cheap tier."""

from __future__ import annotations

import logging
from typing import Any

from core.domain.knowledge import Summarize

logger = logging.getLogger(__name__)

#: The LLM tier scans use; the same cheap tier memory extraction uses.
SCAN_LLM_ROLE = "classification"


def llm_summarizer() -> Summarize | None:
    """A prompt → text callable on the cheap tier, or ``None`` when no LLM is configured."""
    try:
        from core.llm.factory import LLMRole, get_llm

        llm: Any = get_llm(LLMRole(SCAN_LLM_ROLE))
    except Exception:  # noqa: BLE001 - a scan without a model still writes its facts
        logger.debug("[knowledge] scan LLM unavailable", exc_info=True)
        return None

    def summarize(prompt: str) -> str:
        result = llm.invoke(prompt)
        content = getattr(result, "content", result)
        return content if isinstance(content, str) else str(content)

    return summarize


__all__ = ["SCAN_LLM_ROLE", "llm_summarizer"]

"""Shared OpenSRE system prompt loaded from ``opensre_system_prompt.md``.

The Markdown lives beside this loader so every agent path imports one shared
base without reaching through the action package.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPT_FILENAME = "opensre_system_prompt.md"
_PROMPT_PATH = Path(__file__).with_name(_PROMPT_FILENAME)


@lru_cache(maxsize=1)
def load_opensre_system_prompt() -> str:
    """Return the bundled system prompt markdown."""
    return _PROMPT_PATH.read_text(encoding="utf-8")


OPENSRE_SYSTEM_PROMPT = load_opensre_system_prompt()

__all__ = (
    "OPENSRE_SYSTEM_PROMPT",
    "_PROMPT_FILENAME",
    "_PROMPT_PATH",
    "load_opensre_system_prompt",
)

"""
LLM wrapper and response parsers.

Handles streaming and structured parsing of LLM responses.
"""

import os
from dataclasses import dataclass
from typing import Callable
from langchain_anthropic import ChatAnthropic


# ─────────────────────────────────────────────────────────────────────────────
# Data Types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RootCauseResult:
    root_cause: str
    confidence: float


@dataclass(frozen=True)
class InterpretationResult:
    bullets: list[str]
    raw: str


# ─────────────────────────────────────────────────────────────────────────────
# LLM Client
# ─────────────────────────────────────────────────────────────────────────────

_llm: ChatAnthropic | None = None


def get_llm() -> ChatAnthropic:
    """Get or create the LLM client singleton."""
    global _llm
    if _llm is None:
        _llm = ChatAnthropic(
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            max_tokens=1024,
        )
    return _llm


def stream_completion(prompt: str, on_chunk: Callable[[str], None] | None = None) -> str:
    """
    Stream a completion from the LLM.
    
    Args:
        prompt: The prompt to send
        on_chunk: Optional callback for each chunk (for UI updates)
    
    Returns:
        Complete response text
    """
    llm = get_llm()
    content = ""
    for chunk in llm.stream(prompt):
        chunk_text = chunk.content
        content += chunk_text
        if on_chunk and chunk_text.strip():
            on_chunk(chunk_text)
    return content


# ─────────────────────────────────────────────────────────────────────────────
# Parsers
# ─────────────────────────────────────────────────────────────────────────────

def parse_bullets(response: str) -> InterpretationResult:
    """Parse bullet points from LLM response."""
    bullets = []
    for line in response.strip().split('\n'):
        line = line.strip()
        # Support both * and - bullet formats
        if line.startswith('*') or line.startswith('-'):
            bullets.append(line)
    return InterpretationResult(bullets=bullets, raw=response)


def parse_root_cause(response: str) -> RootCauseResult:
    """Parse root cause and confidence from LLM response."""
    root_cause = "Unable to determine root cause"
    confidence = 0.5
    
    if "ROOT_CAUSE:" in response:
        parts = response.split("ROOT_CAUSE:")[1]
        if "CONFIDENCE:" in parts:
            root_cause = parts.split("CONFIDENCE:")[0].strip()
            conf_str = parts.split("CONFIDENCE:")[1].strip().split()[0].replace("%", "")
            try:
                confidence = float(conf_str) / 100
            except ValueError:
                confidence = 0.8
        else:
            root_cause = parts.strip()
    
    return RootCauseResult(root_cause=root_cause, confidence=confidence)


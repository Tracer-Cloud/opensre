"""
LLM wrapper and response parsers.

Handles streaming and structured parsing of LLM responses.
"""

import os
from dataclasses import dataclass

from langchain_anthropic import ChatAnthropic

# ─────────────────────────────────────────────────────────────────────────────
# Data Types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RootCauseResult:
    root_cause: str
    confidence: float
    validated_claims: list[str]
    non_validated_claims: list[str]
    causal_chain: list[str]


# ─────────────────────────────────────────────────────────────────────────────
# LLM Client
# ─────────────────────────────────────────────────────────────────────────────

_llm: ChatAnthropic | None = None


def get_llm() -> ChatAnthropic:
    """
    Get or create the LLM client singleton.

    LangSmith tracking is always enabled.
    All LLM calls will be tracked in LangSmith.
    """
    global _llm
    if _llm is None:
        _llm = ChatAnthropic(
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            max_tokens=1024,
        )
    return _llm


# ─────────────────────────────────────────────────────────────────────────────
# Parsers
# ─────────────────────────────────────────────────────────────────────────────


def parse_root_cause(response: str) -> RootCauseResult:
    """Parse root cause, claims, and confidence from LLM response."""
    root_cause = "Unable to determine root cause"
    confidence = 0.5
    validated_claims: list[str] = []
    non_validated_claims: list[str] = []
    causal_chain: list[str] = []

    if "ROOT_CAUSE:" in response:
        parts = response.split("ROOT_CAUSE:")[1]

        # Extract validated claims
        if "VALIDATED_CLAIMS:" in parts:
            validated_section = parts.split("VALIDATED_CLAIMS:")[1]
            if "NON_VALIDATED_CLAIMS:" in validated_section:
                validated_text = validated_section.split("NON_VALIDATED_CLAIMS:")[0]
            elif "CAUSAL_CHAIN:" in validated_section:
                validated_text = validated_section.split("CAUSAL_CHAIN:")[0]
            else:
                validated_text = (
                    validated_section.split("CONFIDENCE:")[0]
                    if "CONFIDENCE:" in validated_section
                    else validated_section
                )

            for line in validated_text.strip().split("\n"):
                line = line.strip().lstrip("*-• ").strip()
                if (
                    line
                    and not line.startswith("NON_VALIDATED")
                    and not line.startswith("CAUSAL_CHAIN")
                ):
                    validated_claims.append(line)

        # Extract non-validated claims
        if "NON_VALIDATED_CLAIMS:" in parts:
            non_validated_section = parts.split("NON_VALIDATED_CLAIMS:")[1]
            if "CAUSAL_CHAIN:" in non_validated_section:
                non_validated_text = non_validated_section.split("CAUSAL_CHAIN:")[0]
            else:
                non_validated_text = (
                    non_validated_section.split("CONFIDENCE:")[0]
                    if "CONFIDENCE:" in non_validated_section
                    else non_validated_section
                )

            for line in non_validated_text.strip().split("\n"):
                line = line.strip().lstrip("*-• ").strip()
                if (
                    line
                    and not line.startswith("CAUSAL_CHAIN")
                    and not line.startswith("CONFIDENCE")
                ):
                    non_validated_claims.append(line)

        # Extract causal chain
        if "CAUSAL_CHAIN:" in parts:
            causal_section = parts.split("CAUSAL_CHAIN:")[1]
            causal_text = (
                causal_section.split("CONFIDENCE:")[0]
                if "CONFIDENCE:" in causal_section
                else causal_section
            )

            for line in causal_text.strip().split("\n"):
                line = line.strip().lstrip("*-• ").strip()
                if line and not line.startswith("CONFIDENCE"):
                    causal_chain.append(line)

        # Extract confidence
        if "CONFIDENCE:" in parts:
            conf_str = parts.split("CONFIDENCE:")[1].strip().split()[0].replace("%", "")
            try:
                confidence = float(conf_str) / 100
            except ValueError:
                confidence = 0.8

        # Build root_cause text from all sections
        root_cause_parts = []
        if validated_claims:
            root_cause_parts.append(
                "VALIDATED CLAIMS:\n" + "\n".join(f"* {c}" for c in validated_claims)
            )
        if non_validated_claims:
            root_cause_parts.append(
                "NON-VALIDATED CLAIMS:\n" + "\n".join(f"* {c}" for c in non_validated_claims)
            )
        if causal_chain:
            root_cause_parts.append("CAUSAL CHAIN:\n" + "\n".join(f"* {c}" for c in causal_chain))

        if root_cause_parts:
            root_cause = "\n\n".join(root_cause_parts)
        else:
            # Fallback to old format
            root_cause = (
                parts.split("CONFIDENCE:")[0].strip() if "CONFIDENCE:" in parts else parts.strip()
            )

    return RootCauseResult(
        root_cause=root_cause,
        confidence=confidence,
        validated_claims=validated_claims,
        non_validated_claims=non_validated_claims,
        causal_chain=causal_chain,
    )

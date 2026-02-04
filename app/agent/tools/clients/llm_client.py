"""
LLM wrapper and response parsers.

Handles structured parsing of LLM responses.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from anthropic import Anthropic
from pydantic import ValidationError

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


@dataclass(frozen=True)
class LLMResponse:
    content: str


class LLMClient:
    def __init__(self, *, model: str, max_tokens: int = 1024, temperature: float | None = None) -> None:
        self._client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def with_config(self, **_kwargs) -> LLMClient:
        return self

    def with_structured_output(self, model: type) -> StructuredOutputClient:
        return StructuredOutputClient(self, model)

    def bind_tools(self, _tools: list) -> LLMClient:
        return self

    def invoke(self, prompt_or_messages: Any) -> LLMResponse:
        system, messages = _normalize_messages(prompt_or_messages)
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature

        response = self._client.messages.create(**kwargs)
        content = _extract_text(response)
        return LLMResponse(content=content)


class StructuredOutputClient:
    def __init__(self, base: LLMClient, model: type) -> None:
        self._base = base
        self._model = model

    def with_config(self, **_kwargs) -> StructuredOutputClient:
        return self

    def invoke(self, prompt: str) -> Any:
        schema = self._model.model_json_schema()
        schema_json = json.dumps(schema, indent=2)
        wrapped_prompt = (
            f"{prompt}\n\n"
            "Return ONLY valid JSON that matches this schema:\n"
            f"{schema_json}\n"
        )
        response = self._base.invoke(wrapped_prompt)
        payload = _extract_json_payload(response.content)
        try:
            return self._model.model_validate(payload)
        except ValidationError:
            if isinstance(payload, list) and "actions" in self._model.model_fields:
                fallback = {"actions": payload, "rationale": "LLM returned actions only."}
                return self._model.model_validate(fallback)
            raise


def _normalize_messages(prompt_or_messages: Any) -> tuple[str | None, list[dict[str, str]]]:
    if isinstance(prompt_or_messages, list):
        system_parts: list[str] = []
        messages: list[dict[str, str]] = []
        for msg in prompt_or_messages:
            if isinstance(msg, dict):
                role = msg.get("role", "user")
                content = msg.get("content", "")
            else:
                role = getattr(msg, "role", "user")
                content = getattr(msg, "content", "")
            if role == "system":
                system_parts.append(str(content))
            else:
                messages.append({"role": str(role), "content": str(content)})
        return ("\n".join(system_parts) if system_parts else None, messages)

    return None, [{"role": "user", "content": str(prompt_or_messages)}]


def _extract_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    text = "".join(parts).strip()
    return text or str(response)


def _safe_json_loads(payload: str) -> Any:
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return json.loads(payload, strict=False)


def _extract_json_payload(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    try:
        return _safe_json_loads(cleaned)
    except json.JSONDecodeError:
        pass

    obj_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if obj_match:
        try:
            return _safe_json_loads(obj_match.group(0))
        except json.JSONDecodeError:
            pass

    list_match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if list_match:
        try:
            return _safe_json_loads(list_match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError("LLM did not return valid JSON payload")


# ─────────────────────────────────────────────────────────────────────────────
# LLM Client
# ─────────────────────────────────────────────────────────────────────────────

_llm: LLMClient | None = None
_fast_llm: LLMClient | None = None


def get_llm(use_fast_model: bool = False) -> LLMClient:
    """
    Get or create the LLM client singleton.

    LangSmith tracking is always enabled.
    All LLM calls will be tracked in LangSmith.

    Args:
        use_fast_model: If True and memory is available, use Claude Haiku (5-10x faster)
                       for scenarios with strong memory guidance

    Returns:
        LLM client configured for the appropriate model
    """
    if use_fast_model:
        from app.agent.memory import is_memory_enabled

        if is_memory_enabled():
            global _fast_llm
            if _fast_llm is None:
                _fast_llm = LLMClient(
                    model="claude-3-haiku-20240307",
                    max_tokens=1024,
                    temperature=0.3,
                )
                print("[MEMORY] Using fast model (Haiku) with memory guidance")
            return _fast_llm

    global _llm
    if _llm is None:
        _llm = LLMClient(
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

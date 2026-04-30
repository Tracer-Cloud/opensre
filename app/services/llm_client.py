"""
LLM wrapper and response parsers.

Handles structured parsing of LLM responses.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.integrations.llm_cli.registry import CLIProviderRegistration

from anthropic import Anthropic, AnthropicBedrock, AuthenticationError
from openai import AuthenticationError as OpenAIAuthError
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from app.config import (
    ANTHROPIC_LLM_CONFIG,
    GEMINI_BASE_URL,
    MINIMAX_BASE_URL,
    NVIDIA_BASE_URL,
    OPENAI_LLM_CONFIG,
    OPENROUTER_BASE_URL,
    LLMSettings,
)
from app.llm_credentials import resolve_llm_api_key

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Data Types
# ─────────────────────────────────────────────────────────────────────────────


_VALID_ROOT_CAUSE_CATEGORIES = frozenset(
    {
        "configuration_error",
        "code_defect",
        "data_quality",
        "resource_exhaustion",
        "dependency_failure",
        "infrastructure",
        "healthy",
        "unknown",
    }
)


@dataclass(frozen=True)
class RootCauseResult:
    root_cause: str
    root_cause_category: str
    validated_claims: list[str]
    non_validated_claims: list[str]
    causal_chain: list[str]


@dataclass(frozen=True)
class LLMResponse:
    content: str


class LLMClient:
    def __init__(
        self, *, model: str, max_tokens: int = 1024, temperature: float | None = None
    ) -> None:
        api_key = resolve_llm_api_key("ANTHROPIC_API_KEY")
        self._api_key = api_key
        self._client = Anthropic(api_key=api_key, timeout=60.0)
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def with_config(self, **_kwargs) -> LLMClient:
        return self

    def with_structured_output(self, model: type[BaseModel]) -> StructuredOutputClient:
        return StructuredOutputClient(self, model)

    def bind_tools(self, _tools: list) -> LLMClient:
        return self

    def _ensure_client(self) -> None:
        api_key = resolve_llm_api_key("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Missing ANTHROPIC_API_KEY. Set it in your environment, .env, or secure local keychain before running LLM steps."
            )
        if api_key != self._api_key:
            self._api_key = api_key
            self._client = Anthropic(api_key=api_key, timeout=60.0)

    def invoke(self, prompt_or_messages: Any) -> LLMResponse:
        self._ensure_client()
        system, messages = _normalize_messages(prompt_or_messages)

        from app.guardrails.engine import GuardrailBlockedError, get_guardrail_engine

        engine = get_guardrail_engine()
        if engine.is_active:
            for msg in messages:
                msg["content"] = engine.apply(msg["content"])
            if system:
                system = engine.apply(system)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature

        backoff_seconds = 1.0
        max_attempts = 3
        last_err: Exception | None = None
        for attempt in range(max_attempts):
            try:
                response = self._client.messages.create(**kwargs)
                break
            except AuthenticationError as err:
                raise RuntimeError(
                    "Anthropic authentication failed. Check ANTHROPIC_API_KEY in your environment or .env."
                ) from err
            except GuardrailBlockedError:
                raise
            except Exception as err:
                last_err = err
                if attempt == max_attempts - 1:
                    raise RuntimeError(_format_anthropic_retry_error(err)) from err
                time.sleep(backoff_seconds)
                backoff_seconds *= 2
        else:
            raise RuntimeError("LLM invocation failed without a concrete error") from last_err

        content = _extract_text(response)
        return LLMResponse(content=content)


class BedrockLLMClient:
    """LLM client using Anthropic models via Amazon Bedrock (IAM auth, no API key)."""

    def __init__(
        self, *, model: str, max_tokens: int = 1024, temperature: float | None = None
    ) -> None:
        self._client = AnthropicBedrock(aws_region=os.getenv("AWS_REGION", "us-east-1"))
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def with_config(self, **_kwargs: Any) -> BedrockLLMClient:
        return self

    def with_structured_output(self, model: type[BaseModel]) -> StructuredOutputClient:
        return StructuredOutputClient(self, model)

    def bind_tools(self, _tools: list[Any]) -> BedrockLLMClient:
        return self

    def invoke(self, prompt_or_messages: Any) -> LLMResponse:
        system, messages = _normalize_messages(prompt_or_messages)

        from app.guardrails.engine import GuardrailBlockedError, get_guardrail_engine

        engine = get_guardrail_engine()
        if engine.is_active:
            for msg in messages:
                msg["content"] = engine.apply(msg["content"])
            if system:
                system = engine.apply(system)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature

        backoff_seconds = 1.0
        max_attempts = 3
        last_err: Exception | None = None
        for attempt in range(max_attempts):
            try:
                response = self._client.messages.create(**kwargs)
                break
            except GuardrailBlockedError:
                raise
            except Exception as err:
                last_err = err
                if attempt == max_attempts - 1:
                    raise RuntimeError(
                        f"Bedrock API request failed after {max_attempts} attempts: {type(err).__name__}: {err}"
                    ) from err
                time.sleep(backoff_seconds)
                backoff_seconds *= 2
        else:
            raise RuntimeError("Bedrock invocation failed without a concrete error") from last_err

        content = _extract_text(response)
        return LLMResponse(content=content)


def _format_anthropic_retry_error(err: Exception) -> str:
    """Format a user-facing Anthropic retry failure message."""
    error_name = type(err).__name__
    status_code = getattr(err, "status_code", None)
    if error_name == "APIConnectionError":
        return (
            "Anthropic API connection failed after multiple retries. "
            "Check network access and try again."
        )
    if status_code == 529:
        return (
            "Anthropic API is overloaded (HTTP 529) after multiple retries. "
            "Try again in a few seconds."
        )
    return f"Anthropic API request failed after multiple retries: {error_name}."


def _uses_max_completion_tokens(model: str) -> bool:
    """Reasoning models (o1, o3, o4, gpt-5 series) require max_completion_tokens."""
    return model.startswith(("o1", "o3", "o4", "gpt-5"))


class OpenAILLMClient:
    def __init__(
        self,
        *,
        model: str,
        max_tokens: int = 1024,
        temperature: float | None = None,
        base_url: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        api_key_default: str = "",
    ) -> None:
        api_key = resolve_llm_api_key(api_key_env) or api_key_default
        self._api_key = api_key
        self._api_key_default = api_key_default
        self._base_url = base_url
        self._api_key_env = api_key_env
        self._provider_label = api_key_env.removesuffix("_API_KEY").replace("_", " ").title()
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0)
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def with_config(self, **_kwargs) -> OpenAILLMClient:
        return self

    def with_structured_output(self, model: type[BaseModel]) -> StructuredOutputClient:
        return StructuredOutputClient(self, model)

    def bind_tools(self, _tools: list) -> OpenAILLMClient:
        return self

    def _ensure_client(self) -> None:
        api_key = resolve_llm_api_key(self._api_key_env) or self._api_key_default
        if not api_key:
            raise RuntimeError(
                f"Missing {self._api_key_env}. Set it in your environment, .env, or secure local keychain before running LLM steps."
            )
        if api_key != self._api_key:
            self._api_key = api_key
            self._client = OpenAI(api_key=api_key, base_url=self._base_url, timeout=60.0)

    def invoke(self, prompt_or_messages: Any) -> LLMResponse:
        self._ensure_client()
        messages = _normalize_messages_openai(prompt_or_messages)

        from app.guardrails.engine import GuardrailBlockedError, get_guardrail_engine

        engine = get_guardrail_engine()
        if engine.is_active:
            for msg in messages:
                msg["content"] = engine.apply(msg["content"])

        token_param = (
            "max_completion_tokens" if _uses_max_completion_tokens(self._model) else "max_tokens"
        )
        kwargs: dict[str, Any] = {
            "model": self._model,
            token_param: self._max_tokens,
            "messages": messages,
        }
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature

        backoff_seconds = 1.0
        max_attempts = 3
        last_err: Exception | None = None
        for attempt in range(max_attempts):
            try:
                response = self._client.chat.completions.create(**kwargs)
                break
            except OpenAIAuthError as err:
                raise RuntimeError(
                    f"{self._provider_label} authentication failed. Check {self._api_key_env} in your environment, .env, or secure local keychain."
                ) from err
            except GuardrailBlockedError:
                raise
            except Exception as err:
                last_err = err
                if attempt == max_attempts - 1:
                    raise RuntimeError(
                        "LLM API request failed after multiple retries. Try again in a few seconds."
                    ) from err
                time.sleep(backoff_seconds)
                backoff_seconds *= 2
        else:
            raise RuntimeError("LLM invocation failed without a concrete error") from last_err

        if not response.choices:
            raise RuntimeError("OpenAI API returned an empty choices list")
        content = response.choices[0].message.content or ""
        return LLMResponse(content=content.strip())


class StructuredOutputClient:
    """Wraps any LLM client with `.invoke` (API or CLI subprocess) for Pydantic JSON parsing."""

    def __init__(self, base: Any, model: type[BaseModel]) -> None:
        self._base = base
        self._model = model

    def with_config(self, **_kwargs) -> StructuredOutputClient:
        return self

    def invoke(self, prompt: str) -> Any:
        schema = self._model.model_json_schema()
        schema_json = json.dumps(schema, indent=2)
        wrapped_prompt = (
            f"{prompt}\n\nReturn ONLY valid JSON that matches this schema:\n{schema_json}\n"
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


class SupportsLLMInvoke(Protocol):
    def with_config(self, **_kwargs: Any) -> SupportsLLMInvoke:
        pass

    def with_structured_output(self, model: type[BaseModel]) -> Any:
        pass

    def bind_tools(self, _tools: list[Any]) -> SupportsLLMInvoke:
        pass

    def invoke(self, prompt_or_messages: Any) -> LLMResponse:
        pass


def _normalize_messages_openai(prompt_or_messages: Any) -> list[dict[str, str]]:
    if isinstance(prompt_or_messages, list):
        messages: list[dict[str, str]] = []
        for msg in prompt_or_messages:
            if isinstance(msg, dict):
                role = msg.get("role", "user")
                content = msg.get("content", "")
            else:
                role = getattr(msg, "role", "user")
                content = getattr(msg, "content", "")
            messages.append({"role": str(role), "content": str(content)})
        return messages
    return [{"role": "user", "content": str(prompt_or_messages)}]


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
        logger.debug("Direct JSON parse failed, trying regex extraction")

    obj_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if obj_match:
        try:
            return _safe_json_loads(obj_match.group(0))
        except json.JSONDecodeError:
            logger.debug("Object regex JSON parse failed, trying array extraction")

    list_match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if list_match:
        try:
            return _safe_json_loads(list_match.group(0))
        except json.JSONDecodeError:
            logger.debug("Array regex JSON parse also failed")

    raise ValueError("LLM did not return valid JSON payload")


# ─────────────────────────────────────────────────────────────────────────────
# LLM Client
# ─────────────────────────────────────────────────────────────────────────────

# Protocol keeps static type safety for CLI-backed clients without runtime import cycles.
_LLMClientType = LLMClient | OpenAILLMClient | BedrockLLMClient | SupportsLLMInvoke
_llm: _LLMClientType | None = None
_llm_for_tools: _LLMClientType | None = None


def reset_llm_singletons() -> None:
    """Clear cached LLM clients (tests, benchmarks, alternate configs)."""
    global _llm, _llm_for_tools
    _llm = None
    _llm_for_tools = None


def _get_cli_provider_registration(provider: str) -> CLIProviderRegistration | None:
    """Local import avoids package import cycle (llm_cli __init__ → runner → llm_client)."""
    from app.integrations.llm_cli.registry import get_cli_provider_registration

    return get_cli_provider_registration(provider)


def _create_llm_client(model_type: str) -> _LLMClientType:
    settings = LLMSettings.from_env()
    provider = settings.provider
    if provider == "openai":
        config = OPENAI_LLM_CONFIG
        model = (
            settings.openai_reasoning_model
            if model_type == "reasoning"
            else settings.openai_toolcall_model
        )
        return OpenAILLMClient(model=model, max_tokens=config.max_tokens)
    elif provider == "openrouter":
        from app.config import OPENROUTER_LLM_CONFIG

        config = OPENROUTER_LLM_CONFIG
        model = (
            settings.openrouter_reasoning_model
            if model_type == "reasoning"
            else settings.openrouter_toolcall_model
        )
        return OpenAILLMClient(
            model=model,
            max_tokens=config.max_tokens,
            base_url=OPENROUTER_BASE_URL,
            api_key_env="OPENROUTER_API_KEY",
        )
    elif provider == "gemini":
        from app.config import GEMINI_LLM_CONFIG

        config = GEMINI_LLM_CONFIG
        model = (
            settings.gemini_reasoning_model
            if model_type == "reasoning"
            else settings.gemini_toolcall_model
        )
        return OpenAILLMClient(
            model=model,
            max_tokens=config.max_tokens,
            base_url=GEMINI_BASE_URL,
            api_key_env="GEMINI_API_KEY",
        )
    elif provider == "nvidia":
        from app.config import NVIDIA_LLM_CONFIG

        config = NVIDIA_LLM_CONFIG
        model = (
            settings.nvidia_reasoning_model
            if model_type == "reasoning"
            else settings.nvidia_toolcall_model
        )
        return OpenAILLMClient(
            model=model,
            max_tokens=config.max_tokens,
            base_url=NVIDIA_BASE_URL,
            api_key_env="NVIDIA_API_KEY",
        )
    elif provider == "minimax":
        from app.config import MINIMAX_LLM_CONFIG

        config = MINIMAX_LLM_CONFIG
        model = (
            settings.minimax_reasoning_model
            if model_type == "reasoning"
            else settings.minimax_toolcall_model
        )
        return OpenAILLMClient(
            model=model,
            max_tokens=config.max_tokens,
            base_url=MINIMAX_BASE_URL,
            api_key_env="MINIMAX_API_KEY",
            temperature=1.0,
        )
    elif provider == "ollama":
        from app.config import OLLAMA_LLM_CONFIG

        config = OLLAMA_LLM_CONFIG
        host = settings.ollama_host.rstrip("/")
        return OpenAILLMClient(
            model=settings.ollama_model,
            max_tokens=config.max_tokens,
            base_url=f"{host}/v1",
            api_key_env="OLLAMA_API_KEY",
            api_key_default="ollama",
        )
    elif provider == "bedrock":
        from app.config import BEDROCK_LLM_CONFIG

        config = BEDROCK_LLM_CONFIG
        model = (
            settings.bedrock_reasoning_model
            if model_type == "reasoning"
            else settings.bedrock_toolcall_model
        )
        return BedrockLLMClient(model=model, max_tokens=config.max_tokens)
    elif (cli_reg := _get_cli_provider_registration(provider)) is not None:
        from app.config import DEFAULT_MAX_TOKENS
        from app.integrations.llm_cli.runner import CLIBackedLLMClient

        model_name = os.getenv(cli_reg.model_env_key, "").strip() or None
        return CLIBackedLLMClient(
            cli_reg.adapter_factory(),
            model=model_name,
            max_tokens=DEFAULT_MAX_TOKENS,
            model_type=model_type,
        )
    else:
        config = ANTHROPIC_LLM_CONFIG
        model = (
            settings.anthropic_reasoning_model
            if model_type == "reasoning"
            else settings.anthropic_toolcall_model
        )
        return LLMClient(model=model, max_tokens=config.max_tokens)


def get_llm_for_reasoning() -> _LLMClientType:
    """
    Get or create the LLM client singleton for complex reasoning tasks.

    Uses the full-capability model (e.g., Claude Opus, GPT-4o) for:
    - Root cause diagnosis and multi-step analysis
    - Evidence categorization and claim validation

    Provider is controlled by the LLM_PROVIDER env var (default: anthropic).
    Set LLM_PROVIDER=openai to use OpenAI with OPENAI_API_KEY and OPENAI_REASONING_MODEL.
    """
    global _llm
    if _llm is None:
        _llm = _create_llm_client(model_type="reasoning")
    return _llm


def get_llm_for_tools() -> _LLMClientType:
    """
    Get or create a lightweight LLM client for tool selection and action planning.

    Uses toolcall models (Claude Haiku for Anthropic, GPT-4o mini for OpenAI)
    for lower cost and faster inference on simple routing decisions.
    """
    global _llm_for_tools
    if _llm_for_tools is None:
        _llm_for_tools = _create_llm_client(model_type="toolcall")
    return _llm_for_tools


# ─────────────────────────────────────────────────────────────────────────────
# Parsers
# ─────────────────────────────────────────────────────────────────────────────


def parse_root_cause(response: str) -> RootCauseResult:
    """Parse root cause, category, and claims from LLM response."""
    root_cause = "Unable to determine root cause"
    root_cause_category = "unknown"
    validated_claims: list[str] = []
    non_validated_claims: list[str] = []
    causal_chain: list[str] = []

    if "ROOT_CAUSE_CATEGORY:" in response:
        parts = response.split("ROOT_CAUSE_CATEGORY:", 1)
        if len(parts) > 1:
            after = parts[1]
            for line in after.split("\n"):
                candidate = line.strip().lower()
                if candidate and candidate in _VALID_ROOT_CAUSE_CATEGORIES:
                    root_cause_category = candidate
                    break

    if "ROOT_CAUSE:" in response:
        parts = response.split("ROOT_CAUSE:", 1)
        if len(parts) > 1:
            after = parts[1]
            # Extract the root cause sentence (text before first section header)
            for delimiter in (
                "ROOT_CAUSE_CATEGORY:",
                "VALIDATED_CLAIMS:",
                "NON_VALIDATED_CLAIMS:",
                "CAUSAL_CHAIN:",
            ):
                if delimiter in after:
                    root_cause = after.split(delimiter, 1)[0].strip()
                    break
            else:
                root_cause = after.strip()

            # Extract validated claims
            if "VALIDATED_CLAIMS:" in after:
                validated_section = after.split("VALIDATED_CLAIMS:", 1)[1]
                if "NON_VALIDATED_CLAIMS:" in validated_section:
                    validated_text = validated_section.split("NON_VALIDATED_CLAIMS:", 1)[0]
                elif "CAUSAL_CHAIN:" in validated_section:
                    validated_text = validated_section.split("CAUSAL_CHAIN:", 1)[0]
                else:
                    validated_text = validated_section

                for line in validated_text.strip().split("\n"):
                    line = line.strip().lstrip("*-• ").strip()
                    if (
                        line
                        and not line.startswith("NON_")
                        and not line.startswith("CAUSAL_CHAIN")
                        and not line.startswith("CONFIDENCE")
                        and not line.startswith("ROOT_CAUSE")
                    ):
                        validated_claims.append(line)

            # Extract non-validated claims
            if "NON_VALIDATED_CLAIMS:" in after:
                non_validated_section = after.split("NON_VALIDATED_CLAIMS:", 1)[1]
                for delimiter in ("ALTERNATIVE_HYPOTHESES_CONSIDERED:", "CAUSAL_CHAIN:"):
                    if delimiter in non_validated_section:
                        non_validated_text = non_validated_section.split(delimiter, 1)[0]
                        break
                else:
                    non_validated_text = non_validated_section

                for line in non_validated_text.strip().split("\n"):
                    line = line.strip().lstrip("*-• ").strip()
                    if (
                        line
                        and not line.startswith("CAUSAL_CHAIN")
                        and not line.startswith("ALTERNATIVE")
                    ):
                        non_validated_claims.append(line)

            # Extract causal chain
            if "CAUSAL_CHAIN:" in after:
                causal_section = after.split("CAUSAL_CHAIN:", 1)[1]
                causal_text = causal_section

                for line in causal_text.strip().split("\n"):
                    line = line.strip().lstrip("*-• ").strip()
                    if line and not line.startswith("ALTERNATIVE"):
                        causal_chain.append(line)

    return RootCauseResult(
        root_cause=root_cause,
        root_cause_category=root_cause_category,
        validated_claims=validated_claims,
        non_validated_claims=non_validated_claims,
        causal_chain=causal_chain,
    )

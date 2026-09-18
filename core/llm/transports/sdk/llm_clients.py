"""SDK-backed non-agent LLM clients (Anthropic, OpenAI-compatible, Bedrock).

These handle reasoning, classification, and structured-output tiers.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterator
from typing import Any

import boto3
import botocore.exceptions
from anthropic import (
    Anthropic,
    AnthropicBedrock,
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
)
from anthropic import BadRequestError as AnthropicBadRequestError
from openai import APIConnectionError as OpenAIConnectionError
from openai import APITimeoutError as OpenAITimeoutError
from openai import AuthenticationError as OpenAIAuthError
from openai import BadRequestError as OpenAIBadRequestError
from openai import NotFoundError as OpenAINotFoundError
from openai import OpenAI
from openai import RateLimitError as OpenAIRateLimitError
from pydantic import BaseModel

from core.llm.providers import provider_credentials
from core.llm.providers.bedrock_model_ids import is_anthropic_bedrock_model
from core.llm.shared.llm_retry import (
    extract_retry_after_seconds,
    maybe_raise_credit_exhausted,
)
from core.llm.shared.openai_chat_completions import (
    _RETRY_INITIAL_BACKOFF_SEC,
    _RETRY_MAX_ATTEMPTS,
    LLM_CLIENT_TIMEOUT_SEC,
    normalize_messages_openai,
)
from core.llm.shared.structured_output import (
    StructuredOutputClient,
    json_schema_for_structured_output,
)
from core.llm.shared.usage import llm_response_with_usage
from core.llm.transports.sdk.anthropic_cache import (
    cached_system,
    is_cache_unsupported_error,
    strip_cache_markers,
)
from core.llm.types import LLMResponse

logger = logging.getLogger(__name__)

# Anthropic overloaded — not in stdlib ``http.HTTPStatus`` (non-standard code).
_ANTHROPIC_OVERLOADED_STATUS = 529


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


def _format_anthropic_bad_request(err: AnthropicBadRequestError) -> str:
    """Return a user-facing message for Anthropic HTTP 400 errors."""
    body = getattr(err, "body", None)
    if isinstance(body, dict):
        error_obj = body.get("error", {})
        api_msg = error_obj.get("message") if isinstance(error_obj, dict) else None
        api_msg = api_msg if isinstance(api_msg, str) else ""
        if "usage limit" in api_msg.lower():
            return f"Anthropic API usage limit reached. {api_msg}"
    return f"Anthropic request rejected (HTTP 400): {err.message}"


def _format_anthropic_retry_error(err: Exception) -> str:
    """Format a user-facing Anthropic retry failure message."""
    error_name = type(err).__name__
    status_code = getattr(err, "status_code", None)
    if error_name == "APIConnectionError":
        return (
            "Anthropic API connection failed after multiple retries. "
            "Check network access and try again."
        )
    # Detect overloaded via HTTP status (error-response path) or via body error
    # type (SSE streaming path: the SDK raises APIStatusError from body events
    # where the initial HTTP response was 200, so status_code is absent / not
    # ``_ANTHROPIC_OVERLOADED_STATUS``).
    body = getattr(err, "body", None)
    error_obj = body.get("error") if isinstance(body, dict) else None
    body_error_type = error_obj.get("type", "") if isinstance(error_obj, dict) else ""
    if status_code == _ANTHROPIC_OVERLOADED_STATUS or body_error_type == "overloaded_error":
        return (
            "Anthropic API is overloaded (HTTP 529) after multiple retries. "
            "Try again in a few seconds."
        )
    return f"Anthropic API request failed after multiple retries: {error_name}."


# Substrings that signal an unknown/invalid model name in a 400 response.
# OpenAI:    "The provided model identifier is invalid."
# OpenRouter: "Invalid model name passed in model=<name>. Call `/v1/models`…"
# Detection is an any-match because there is no stable error code across
# providers. Add phrases here when a new provider uses different wording;
# without a matching phrase, an invalid-model 400 falls through to a
# generic error message instead of the specific invalid-model one.
_OPENAI_INVALID_MODEL_IDENTIFIER_PHRASES = (
    "model identifier",  # OpenAI / LiteLLM
    "invalid model id",  # OpenAI
    "invalid model name",  # OpenRouter
)


def _is_openai_invalid_model_identifier(err: OpenAIBadRequestError) -> bool:
    """True if the OpenAIBadRequestError message indicates an unknown model id."""
    msg = (err.message or "").lower()
    return any(phrase in msg for phrase in _OPENAI_INVALID_MODEL_IDENTIFIER_PHRASES)


def _format_openai_connection_error(err: Exception, provider_label: str) -> str:
    """Return a user-facing message for an OpenAI APIConnectionError."""
    if isinstance(err, OpenAITimeoutError):
        return (
            f"{provider_label} API request timed out. "
            "Check that the service is running and responsive at the configured endpoint."
        )
    cause: BaseException | None = err
    cause_text_parts: list[str] = []
    while cause is not None:
        cause_text_parts.append(str(cause).lower())
        next_cause = getattr(cause, "__cause__", None)
        if next_cause is None:
            next_cause = getattr(cause, "__context__", None)
        cause = next_cause

    cause_text = " ".join(cause_text_parts)
    if "ssl" in cause_text or "wrong_version_number" in cause_text or "certificate" in cause_text:
        return (
            f"Cannot connect to {provider_label} API (SSL/TLS error). "
            "Verify the endpoint URL uses HTTPS and that no proxy is stripping TLS."
        )
    return (
        f"Cannot connect to {provider_label} API. "
        "Check your network connection and that the endpoint URL is reachable."
    )


def _uses_max_completion_tokens(model: str) -> bool:
    """Reasoning models (o1, o3, o4, gpt-5 series) require max_completion_tokens."""
    return model.startswith(("o1", "o3", "o4", "gpt-5"))


def _resolve_openai_reasoning_effort(*, model: str, api_key_env: str) -> str | None:
    """Session override for OpenAI reasoning models in the interactive shell."""
    if api_key_env != "OPENAI_API_KEY" or not _uses_max_completion_tokens(model):
        return None
    from config.llm_reasoning_effort import get_active_reasoning_effort

    return get_active_reasoning_effort()


class LLMClient:
    # Best-effort prompt caching: a class default so every instance starts
    # marking; flipped to an instance False for the client's lifetime the
    # first time the provider rejects the cache markers with a 400.
    _cache_markers_enabled = True

    def __init__(
        self,
        *,
        model: str,
        max_tokens: int = 1024,
        temperature: float | None = None,
        base_url: str | None = None,
        api_key_env: str = "ANTHROPIC_API_KEY",
    ) -> None:
        # base_url + api_key_env let a custom-anthropic gateway (Anthropic SDK
        # with a base-URL override) reuse this client; they default to the
        # first-party Anthropic endpoint/key, so existing behavior is unchanged.
        self._api_key_env = api_key_env
        self._base_url = base_url
        api_key = provider_credentials.resolve_llm_api_key(api_key_env)
        self._api_key = api_key
        self._client = self._new_anthropic_client(api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def _new_anthropic_client(self, api_key: str) -> Anthropic:
        client_kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": LLM_CLIENT_TIMEOUT_SEC,
        }
        if self._base_url:
            client_kwargs["base_url"] = self._base_url
        return Anthropic(**client_kwargs)

    def with_config(self, **_kwargs) -> LLMClient:
        return self

    def with_structured_output(self, model: type[BaseModel]) -> StructuredOutputClient:
        return StructuredOutputClient(self, model)

    def invoke_structured(self, model: type[BaseModel], prompt: str) -> str:
        """Constrained JSON via Anthropic ``output_config.format`` (json_schema).

        Uses the same retry policy and usage reporting as :meth:`invoke`: this
        call carries the cached system prefix, so leaving it out of token
        accounting would hide the diagnose stage's spend and cache hits.
        """
        kwargs = self._build_request_kwargs(prompt)
        kwargs["output_config"] = {
            "format": {
                "type": "json_schema",
                "schema": json_schema_for_structured_output(model),
            }
        }
        response = self._create_with_retry(kwargs)
        return llm_response_with_usage(
            _extract_text(response),
            self._model,
            getattr(response, "usage", None),
            input_key="input_tokens",
            output_key="output_tokens",
        ).content

    def _ensure_client(self) -> None:
        api_key = provider_credentials.resolve_llm_api_key(self._api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Missing {self._api_key_env}. Set it in your environment, .env, or secure "
                "local keychain before running LLM steps."
            )
        if api_key != self._api_key:
            self._api_key = api_key
            self._client = self._new_anthropic_client(api_key)

    def _build_request_kwargs(self, prompt_or_messages: Any) -> dict[str, Any]:
        """Refresh credentials, normalize messages, apply guardrails, and build API kwargs.

        Shared by ``invoke`` and ``invoke_stream`` so both paths apply the same
        pre-flight (credential refresh, guardrail redaction, kwargs shape).
        """
        self._ensure_client()
        system, messages = _normalize_messages(prompt_or_messages)

        from infrastructure.safety.guardrails.apply import apply_guardrails_to_messages

        messages, system = apply_guardrails_to_messages(messages, system)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = cached_system(system) if self._cache_markers_enabled else system
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature
        return kwargs

    def invoke(self, prompt_or_messages: Any) -> LLMResponse:
        kwargs = self._build_request_kwargs(prompt_or_messages)
        response = self._create_with_retry(kwargs)

        content = _extract_text(response)
        usage = getattr(response, "usage", None)
        return llm_response_with_usage(
            content,
            self._model,
            usage,
            input_key="input_tokens",
            output_key="output_tokens",
        )

    def _create_with_retry(self, kwargs: dict[str, Any]) -> Any:
        """Call the messages API with the shared backoff and error mapping."""
        # What this request carries, decided before any concurrent turn can
        # clear the shared flag.
        marked = strip_cache_markers(kwargs) != kwargs
        from infrastructure.safety.guardrails.evaluator import GuardrailBlockedError

        backoff_seconds = _RETRY_INITIAL_BACKOFF_SEC
        max_attempts = _RETRY_MAX_ATTEMPTS
        last_err: Exception | None = None
        for attempt in range(max_attempts):
            try:
                return self._client.messages.create(**kwargs)
            except AuthenticationError as err:
                raise RuntimeError(
                    "Anthropic authentication failed. Check ANTHROPIC_API_KEY in your environment or .env."
                ) from err
            except NotFoundError as err:
                raise RuntimeError(
                    f"Anthropic model '{self._model}' was not found. "
                    "Check your configured model name and try again."
                ) from err
            except AnthropicBadRequestError as err:
                # Any bad request may be the markers' fault: Bedrock's
                # ValidationException does not reliably name the field, so the
                # only dependable probe is to retry without them. Markers are
                # disabled only if that retry actually succeeds, leaving genuine
                # bad requests (bad max_tokens, oversized prompt) untouched.
                if marked and is_cache_unsupported_error(err):
                    # Keyed on what this request carried: a concurrent turn may
                    # already have cleared the shared flag. The stripped retry
                    # carries no markers, so this path runs at most once.
                    self._cache_markers_enabled = False
                    logger.warning(
                        "Model '%s' rejected prompt-cache markers; retrying without "
                        "prompt caching (requests will pay full input price).",
                        self._model,
                    )
                    return self._create_with_retry(strip_cache_markers(kwargs))
                raise RuntimeError(_format_anthropic_bad_request(err)) from err
            except GuardrailBlockedError:
                raise
            except Exception as err:
                last_err = err
                if attempt == max_attempts - 1:
                    raise RuntimeError(_format_anthropic_retry_error(err)) from err
                time.sleep(backoff_seconds)
                backoff_seconds *= 2
        raise RuntimeError("LLM invocation failed without a concrete error") from last_err

    def invoke_stream(self, prompt_or_messages: Any) -> Iterator[str]:
        """Yield text chunks as the model emits them.

        Retries transient failures (e.g. ``529 overloaded_error``, network
        blips) **only before any chunk has been yielded** — once the first
        token has reached the caller, retrying would duplicate visible output,
        so any post-emission failure propagates immediately. Auth and
        guardrail errors never retry.
        """
        from infrastructure.safety.guardrails.evaluator import GuardrailBlockedError

        kwargs = self._build_request_kwargs(prompt_or_messages)
        # What this request carries, decided before any concurrent turn can
        # clear the shared flag.
        marked = strip_cache_markers(kwargs) != kwargs

        backoff_seconds = _RETRY_INITIAL_BACKOFF_SEC
        max_attempts = _RETRY_MAX_ATTEMPTS
        for attempt in range(max_attempts):
            emitted = False
            try:
                with self._client.messages.stream(**kwargs) as stream:
                    for text in stream.text_stream:
                        emitted = True
                        yield text
                return
            except AuthenticationError as err:
                raise RuntimeError(
                    "Anthropic authentication failed. Check ANTHROPIC_API_KEY in your environment or .env."
                ) from err
            except NotFoundError as err:
                raise RuntimeError(
                    f"Anthropic model '{self._model}' was not found. "
                    "Check your configured model name and try again."
                ) from err
            except AnthropicBadRequestError as err:
                if not emitted and marked and is_cache_unsupported_error(err):
                    # Keyed on what this request carried, not the shared flag.
                    # Only before the first chunk: retrying after output has
                    # reached the caller would duplicate visible text.
                    self._cache_markers_enabled = False
                    logger.warning(
                        "Model '%s' rejected prompt-cache markers; retrying without "
                        "prompt caching (requests will pay full input price).",
                        self._model,
                    )
                    yield from self.invoke_stream(prompt_or_messages)
                    return
                raise RuntimeError(_format_anthropic_bad_request(err)) from err
            except GuardrailBlockedError:
                raise
            except Exception as err:
                if emitted:
                    # Mid-stream failure: never retry — chunks are already on
                    # the user's screen and a retry would duplicate them.
                    raise
                if attempt == max_attempts - 1:
                    raise RuntimeError(_format_anthropic_retry_error(err)) from err
                time.sleep(backoff_seconds)
                backoff_seconds *= 2


class BedrockLLMClient:
    """LLM client for Amazon Bedrock (IAM auth, no API key).

    Supports **all** Bedrock models:
    - Anthropic Claude models → AnthropicBedrock SDK (existing behaviour)
    - Non-Anthropic models (Mistral, GPT OSS, Llama, etc.) → boto3 ``converse`` API
    """

    # Best-effort prompt caching: Bedrock rejects cache markers with a 400 for
    # Claude models that predate prompt caching, so the first such rejection
    # flips this to an instance False for the client's lifetime.
    _cache_markers_enabled = True

    def __init__(
        self, *, model: str, max_tokens: int = 1024, temperature: float | None = None
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._use_anthropic = is_anthropic_bedrock_model(model)
        self._aws_region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))

        if self._use_anthropic:
            self._anthropic_client: AnthropicBedrock | None = AnthropicBedrock(
                aws_region=self._aws_region
            )
            self._boto3_client: Any = None
        else:
            self._anthropic_client = None
            self._boto3_client = boto3.client("bedrock-runtime", region_name=self._aws_region)

    def with_config(self, **_kwargs: Any) -> BedrockLLMClient:
        return self

    def with_structured_output(self, model: type[BaseModel]) -> StructuredOutputClient:
        return StructuredOutputClient(self, model)

    def _invoke_anthropic(self, prompt_or_messages: Any) -> LLMResponse:
        """Invoke via AnthropicBedrock SDK (Claude models only)."""
        assert self._anthropic_client is not None
        system, messages = _normalize_messages(prompt_or_messages)

        from infrastructure.safety.guardrails.apply import apply_guardrails_to_messages
        from infrastructure.safety.guardrails.evaluator import GuardrailBlockedError

        messages, system = apply_guardrails_to_messages(messages, system)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = cached_system(system) if self._cache_markers_enabled else system
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature

        # What this request carries, decided before any concurrent turn can
        # clear the shared flag.
        marked = strip_cache_markers(kwargs) != kwargs
        backoff_seconds = _RETRY_INITIAL_BACKOFF_SEC
        max_attempts = _RETRY_MAX_ATTEMPTS
        last_err: Exception | None = None
        for attempt in range(max_attempts):
            try:
                response = self._anthropic_client.messages.create(**kwargs)
                break
            except AnthropicBadRequestError as err:
                # Bedrock's ValidationException often does not name the field,
                # so wording alone cannot tell a marker rejection from any other
                # bad request. Retry once stripped and keep markers off only if
                # that actually fixed it.
                if marked and is_cache_unsupported_error(err):
                    # Keyed on what this request carried: a concurrent turn may
                    # already have cleared the shared flag. The stripped retry
                    # carries no markers, so this path runs at most once.
                    self._cache_markers_enabled = False
                    logger.warning(
                        "Bedrock model '%s' rejected prompt-cache markers; retrying "
                        "without prompt caching (requests will pay full input price).",
                        self._model,
                    )
                    return self._invoke_anthropic(prompt_or_messages)
                err_msg = str(err)
                err_msg_lower = err_msg.lower()
                if "on-demand throughput" in err_msg or "inference profile" in err_msg_lower:
                    raise RuntimeError(
                        f"Bedrock model '{self._model}' requires a cross-region inference profile. "
                        f"Try prefixing with 'us.' (e.g. 'us.{self._model}') and update "
                        "BEDROCK_REASONING_MODEL or BEDROCK_TOOLCALL_MODEL."
                    ) from err
                if "usage limits" in err_msg_lower:
                    raise RuntimeError(
                        f"Anthropic billing quota exceeded for Bedrock model '{self._model}'. "
                        "Check your account plan and usage limits."
                    ) from err
                raise RuntimeError(
                    f"Bedrock Anthropic request rejected (HTTP 400) for model "
                    f"'{self._model}': {err.message}"
                ) from err
            except GuardrailBlockedError:
                raise
            except AuthenticationError as err:
                raise RuntimeError(
                    f"Bedrock authentication failed for model '{self._model}'. "
                    "Check AWS credentials, region configuration, and Bedrock access."
                ) from err
            except NotFoundError as err:
                raise RuntimeError(
                    f"Bedrock model '{self._model}' was not found or has reached end-of-life. "
                    "Update BEDROCK_REASONING_MODEL or BEDROCK_TOOLCALL_MODEL to a supported model."
                ) from err
            except PermissionDeniedError as err:
                raise RuntimeError(
                    f"Bedrock model '{self._model}' is not available for your account. "
                    "Check your AWS Marketplace subscription and account permissions, "
                    "or update BEDROCK_REASONING_MODEL / BEDROCK_TOOLCALL_MODEL."
                ) from err
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
        usage = getattr(response, "usage", None)
        return llm_response_with_usage(
            content,
            self._model,
            usage,
            input_key="input_tokens",
            output_key="output_tokens",
        )

    def _invoke_converse(self, prompt_or_messages: Any) -> LLMResponse:
        """Invoke via boto3 converse API (works with all Bedrock models)."""
        assert self._boto3_client is not None
        system, messages = _normalize_messages(prompt_or_messages)

        from infrastructure.safety.guardrails.apply import apply_guardrails_to_messages
        from infrastructure.safety.guardrails.evaluator import GuardrailBlockedError

        messages, system = apply_guardrails_to_messages(messages, system)

        # Convert to converse API message format ({ "text": "..." } blocks only).
        converse_messages = [
            {"role": msg["role"], "content": [{"text": msg["content"]}]} for msg in messages
        ]

        kwargs: dict[str, Any] = {
            "modelId": self._model,
            "messages": converse_messages,
            "inferenceConfig": {"maxTokens": self._max_tokens},
        }
        if system:
            kwargs["system"] = [{"text": system}]
        if self._temperature is not None:
            kwargs["inferenceConfig"]["temperature"] = self._temperature

        backoff_seconds = _RETRY_INITIAL_BACKOFF_SEC
        max_attempts = _RETRY_MAX_ATTEMPTS
        last_err: Exception | None = None
        for attempt in range(max_attempts):
            try:
                response = self._boto3_client.converse(**kwargs)
                break
            except GuardrailBlockedError:
                raise
            except botocore.exceptions.ClientError as err:
                code = err.response.get("Error", {}).get("Code", "")
                if code == "ValidationException":
                    raise RuntimeError(
                        f"Bedrock model ID '{self._model}' is invalid. "
                        "Check BEDROCK_REASONING_MODEL or BEDROCK_TOOLCALL_MODEL."
                    ) from err
                if code == "ResourceNotFoundException":
                    raise RuntimeError(
                        f"Bedrock model '{self._model}' was not found in the configured region. "
                        "Check the model ID, region, or inference profile."
                    ) from err
                if code in ("AccessDeniedException", "UnauthorizedException"):
                    # AccessDeniedException is overloaded on Bedrock: it can mean
                    # missing IAM, missing per-region/per-model Bedrock access
                    # opt-in, or an AWS Marketplace billing problem (e.g.
                    # ``INVALID_PAYMENT_INSTRUMENT``). Surface the upstream
                    # AWS-provided reason so the user knows which one to fix.
                    err_msg = err.response.get("Error", {}).get("Message", "") or ""
                    err_msg_str = str(err_msg)
                    if (
                        "INVALID_PAYMENT_INSTRUMENT" in err_msg_str
                        or "payment instrument" in err_msg_str.lower()
                    ):
                        aws_message = err_msg_str.strip().rstrip(".")
                        detail = f" Cause: {aws_message}." if aws_message else ""
                        raise RuntimeError(
                            f"Access denied for Bedrock model '{self._model}'.{detail} "
                            "A valid AWS payment instrument is required — add a payment method "
                            "to your AWS account or check your AWS Marketplace subscription."
                        ) from err
                    aws_message = err_msg_str.strip().rstrip(".")
                    detail = f" Cause: {aws_message}." if aws_message else ""
                    raise RuntimeError(
                        f"Access denied for Bedrock model '{self._model}'.{detail} "
                        "Check Bedrock model access (per-region opt-in), your "
                        "AWS Marketplace subscription / payment method, and "
                        "IAM permissions."
                    ) from err
                last_err = err
                if attempt == max_attempts - 1:
                    raise RuntimeError(
                        f"Bedrock API request failed after {max_attempts} attempts: {type(err).__name__}: {err}"
                    ) from err
                time.sleep(backoff_seconds)
                backoff_seconds *= 2
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

        # Extract text from converse response
        output_message = response.get("output", {}).get("message", {})
        content_blocks = output_message.get("content", [])
        text_parts: list[str] = []
        for block in content_blocks:
            if "text" in block:
                text_parts.append(block["text"])
        content = "\n".join(text_parts).strip()
        if not content:
            stop_reason = response.get("stopReason")
            logger.warning(
                "Bedrock converse returned no text blocks (stopReason=%s); raw response: %s",
                stop_reason,
                response,
            )
            raise RuntimeError(
                f"Bedrock converse returned no text content (stopReason={stop_reason!r})"
            )
        usage_dict = response.get("usage") if isinstance(response, dict) else None
        return llm_response_with_usage(
            content,
            self._model,
            usage_dict,
            input_key="inputTokens",
            output_key="outputTokens",
        )

    def invoke(self, prompt_or_messages: Any) -> LLMResponse:
        if self._use_anthropic:
            return self._invoke_anthropic(prompt_or_messages)
        return self._invoke_converse(prompt_or_messages)

    def invoke_stream(self, prompt_or_messages: Any) -> Iterator[str]:
        """Yield the full response as one chunk; real streaming is a follow-up.

        Bedrock supports token streaming via ``AnthropicBedrock.messages.stream``
        and ``boto3 converse_stream``, but wiring those paths is deferred —
        the yield-once fallback satisfies the protocol contract.
        """
        yield self.invoke(prompt_or_messages).content


class OpenAILLMClient:
    def __init__(
        self,
        *,
        model: str,
        model_fallback: str | None = None,
        max_tokens: int = 1024,
        temperature: float | None = None,
        base_url: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        api_key_default: str = "",
        default_headers: dict[str, str] | None = None,
        credential_resolver: Callable[[str], str] | None = None,
    ) -> None:
        self._credential_resolver = credential_resolver or provider_credentials.resolve_llm_api_key
        api_key = self._credential_resolver(api_key_env) or api_key_default
        self._api_key = api_key
        self._api_key_default = api_key_default
        self._base_url = base_url
        self._api_key_env = api_key_env
        self._default_headers = default_headers
        self._provider_label = api_key_env.removesuffix("_API_KEY").replace("_", " ").title()
        self._client: OpenAI | None = None
        self._model = model
        fallback = (model_fallback or "").strip()
        self._model_fallback = fallback if fallback and fallback != model else None
        self._max_tokens = max_tokens
        self._temperature = temperature

    def _activate_model_fallback(self) -> bool:
        """Switch to the configured fallback model once and report it."""
        fallback = self._model_fallback
        if not fallback or fallback == self._model:
            return False
        previous = self._model
        self._model = fallback
        logger.warning(
            "%s model '%s' unavailable; falling back to toolcall model '%s'.",
            self._provider_label,
            previous,
            fallback,
        )
        return True

    def _build_client(self, api_key: str) -> OpenAI:
        return OpenAI(
            api_key=api_key,
            base_url=self._base_url,
            timeout=LLM_CLIENT_TIMEOUT_SEC,
            default_headers=self._default_headers,
        )

    def with_config(self, **_kwargs) -> OpenAILLMClient:
        return self

    def with_structured_output(self, model: type[BaseModel]) -> StructuredOutputClient:
        return StructuredOutputClient(self, model)

    def invoke_structured(self, model: type[BaseModel], prompt: str) -> BaseModel:
        """Constrained JSON via OpenAI ``chat.completions.parse`` (strict schema)."""
        from core.llm.hosted_credits import admit_hosted_credits

        admit_hosted_credits()
        kwargs = self._build_request_kwargs(prompt)
        client = self._ensure_client()
        parse_kwargs: dict[str, Any] = {
            "model": kwargs["model"],
            "messages": kwargs["messages"],
            "response_format": model,
        }
        if "max_tokens" in kwargs:
            parse_kwargs["max_tokens"] = kwargs["max_tokens"]
        if "max_completion_tokens" in kwargs:
            parse_kwargs["max_completion_tokens"] = kwargs["max_completion_tokens"]
        if "temperature" in kwargs:
            parse_kwargs["temperature"] = kwargs["temperature"]
        if "reasoning_effort" in kwargs:
            parse_kwargs["reasoning_effort"] = kwargs["reasoning_effort"]
        completion = self._parse_with_retry(client, parse_kwargs)
        # Same accounting as invoke(): this call carries the cached prefix, so
        # leaving it out would hide the diagnose stage's spend and cache hits.
        llm_response_with_usage(
            "",
            self._model,
            getattr(completion, "usage", None),
            input_key="prompt_tokens",
            output_key="completion_tokens",
        )
        message = completion.choices[0].message
        if getattr(message, "refusal", None):
            raise RuntimeError(f"OpenAI refused structured output: {message.refusal}")
        parsed = message.parsed
        if not isinstance(parsed, BaseModel):
            raise RuntimeError("OpenAI structured output returned no parsed payload")
        return parsed

    def _parse_with_retry(self, client: OpenAI, parse_kwargs: dict[str, Any]) -> Any:
        """Structured parse with the same backoff as :meth:`invoke`."""
        backoff_seconds = _RETRY_INITIAL_BACKOFF_SEC
        last_err: Exception | None = None
        for attempt in range(_RETRY_MAX_ATTEMPTS):
            try:
                return client.chat.completions.parse(**parse_kwargs)
            except (OpenAIAuthError, OpenAINotFoundError, OpenAIBadRequestError):
                raise
            except Exception as err:
                maybe_raise_credit_exhausted(getattr(self, "_provider_label", "OpenAI"), err)
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise
                time.sleep(backoff_seconds)
                backoff_seconds *= 2
        raise RuntimeError("OpenAI structured invocation failed") from last_err

    def _ensure_client(self) -> OpenAI:
        api_key = self._credential_resolver(self._api_key_env) or self._api_key_default
        if not api_key:
            raise RuntimeError(
                f"Missing {self._api_key_env}. Set it in your environment, .env, or secure local keychain before running LLM steps."
            )
        if self._client is None or api_key != self._api_key:
            self._api_key = api_key
            self._client = self._build_client(api_key)
        return self._client

    def _build_request_kwargs(self, prompt_or_messages: Any) -> dict[str, Any]:
        """Refresh credentials, normalize messages, apply guardrails, and build API kwargs.

        Shared by ``invoke`` and ``invoke_stream`` so both paths apply the same
        pre-flight (credential refresh, guardrail redaction, kwargs shape).
        """
        self._ensure_client()
        messages = normalize_messages_openai(prompt_or_messages)

        from infrastructure.safety.guardrails.apply import apply_guardrails_to_messages

        messages, _ = apply_guardrails_to_messages(messages)

        token_param = (
            "max_completion_tokens" if _uses_max_completion_tokens(self._model) else "max_tokens"
        )
        kwargs: dict[str, Any] = {
            "model": self._model,
            token_param: self._max_tokens,
            "messages": messages,
        }
        reasoning_effort = _resolve_openai_reasoning_effort(
            model=self._model,
            api_key_env=self._api_key_env,
        )
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature
        return kwargs

    def invoke(self, prompt_or_messages: Any) -> LLMResponse:
        from core.llm.hosted_credits import admit_hosted_credits
        from infrastructure.safety.guardrails.evaluator import GuardrailBlockedError

        admit_hosted_credits()
        # Build kwargs first (also calls _ensure_client internally) so the
        # captured client below reflects the latest key — guards against a
        # rotation between the two _ensure_client invocations.
        kwargs = self._build_request_kwargs(prompt_or_messages)
        client = self._ensure_client()

        backoff_seconds = _RETRY_INITIAL_BACKOFF_SEC
        max_attempts = _RETRY_MAX_ATTEMPTS
        last_err: Exception | None = None
        for attempt in range(max_attempts):
            try:
                response = client.chat.completions.create(**kwargs)
                break
            except OpenAIAuthError as err:
                raise RuntimeError(
                    f"{self._provider_label} authentication failed. Check {self._api_key_env} in your environment, .env, or secure local keychain."
                ) from err
            except OpenAINotFoundError as err:
                if self._activate_model_fallback():
                    kwargs = self._build_request_kwargs(prompt_or_messages)
                    continue
                raise RuntimeError(
                    f"{self._provider_label} model '{self._model}' was not found. "
                    "Check your configured model name or endpoint."
                ) from err
            except OpenAIBadRequestError as err:
                if _is_openai_invalid_model_identifier(err) and self._activate_model_fallback():
                    kwargs = self._build_request_kwargs(prompt_or_messages)
                    continue
                if _is_openai_invalid_model_identifier(err):
                    raise RuntimeError(
                        f"{self._provider_label} model '{self._model}' was not found. "
                        "Check your configured model name or endpoint."
                    ) from err
                raise RuntimeError(
                    f"{self._provider_label} request rejected (HTTP 400): {err.message}"
                ) from err
            except GuardrailBlockedError:
                raise
            except OpenAITimeoutError as err:
                if attempt == max_attempts - 1:
                    raise RuntimeError(
                        _format_openai_connection_error(err, self._provider_label)
                    ) from err
                time.sleep(backoff_seconds)
                backoff_seconds *= 2
            except OpenAIConnectionError as err:
                raise RuntimeError(
                    _format_openai_connection_error(err, self._provider_label)
                ) from err
            except OpenAIRateLimitError as err:
                maybe_raise_credit_exhausted(self._provider_label, err)
                last_err = err
                if attempt == max_attempts - 1:
                    raise RuntimeError(
                        f"{self._provider_label} rate limit exceeded (HTTP 429) after multiple retries. "
                        "Check your quota and billing details."
                    ) from err
                suggested = extract_retry_after_seconds(err) or 0.0
                wait = max(suggested, backoff_seconds)
                time.sleep(wait)
                backoff_seconds = wait * 2
            except Exception as err:
                maybe_raise_credit_exhausted(self._provider_label, err)
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
        message = response.choices[0].message
        content = message.content or ""
        usage = getattr(response, "usage", None)
        return llm_response_with_usage(
            content.strip(),
            self._model,
            usage,
            input_key="prompt_tokens",
            output_key="completion_tokens",
        )

    def invoke_stream(self, prompt_or_messages: Any) -> Iterator[str]:
        """Yield text chunks as the model emits them.

        Retries transient failures (overloaded, network blips) **only before
        any chunk has been yielded** — once a token has reached the caller,
        retrying would duplicate visible output, so post-emission failures
        propagate. Auth and guardrail errors never retry.
        """
        from core.llm.hosted_credits import admit_hosted_credits
        from infrastructure.safety.guardrails.evaluator import GuardrailBlockedError

        admit_hosted_credits()
        # Build kwargs first (also calls _ensure_client internally) so the
        # captured client below reflects the latest key — same rotation
        # guard as ``invoke``.
        kwargs = self._build_request_kwargs(prompt_or_messages)
        client = self._ensure_client()

        backoff_seconds = _RETRY_INITIAL_BACKOFF_SEC
        max_attempts = _RETRY_MAX_ATTEMPTS
        for attempt in range(max_attempts):
            emitted = False
            try:
                for chunk in client.chat.completions.create(stream=True, **kwargs):
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta.content
                    if delta:
                        emitted = True
                        yield delta
                return
            except OpenAIAuthError as err:
                raise RuntimeError(
                    f"{self._provider_label} authentication failed. Check {self._api_key_env} in your environment, .env, or secure local keychain."
                ) from err
            except OpenAINotFoundError as err:
                if not emitted and self._activate_model_fallback():
                    kwargs = self._build_request_kwargs(prompt_or_messages)
                    continue
                raise RuntimeError(
                    f"{self._provider_label} model '{self._model}' was not found. "
                    "Check your configured model name or endpoint."
                ) from err
            except OpenAIBadRequestError as err:
                if (
                    not emitted
                    and _is_openai_invalid_model_identifier(err)
                    and self._activate_model_fallback()
                ):
                    kwargs = self._build_request_kwargs(prompt_or_messages)
                    continue
                if _is_openai_invalid_model_identifier(err):
                    raise RuntimeError(
                        f"{self._provider_label} model '{self._model}' was not found. "
                        "Check your configured model name or endpoint."
                    ) from err
                raise RuntimeError(
                    f"{self._provider_label} request rejected (HTTP 400): {err.message}"
                ) from err
            except GuardrailBlockedError:
                raise
            except OpenAITimeoutError as err:
                if emitted:
                    raise
                if attempt == max_attempts - 1:
                    raise RuntimeError(
                        _format_openai_connection_error(err, self._provider_label)
                    ) from err
                time.sleep(backoff_seconds)
                backoff_seconds *= 2
            except OpenAIConnectionError as err:
                if emitted:
                    raise
                raise RuntimeError(
                    _format_openai_connection_error(err, self._provider_label)
                ) from err
            except OpenAIRateLimitError as err:
                maybe_raise_credit_exhausted(self._provider_label, err)
                if emitted:
                    raise
                if attempt == max_attempts - 1:
                    raise RuntimeError(
                        f"{self._provider_label} rate limit exceeded (HTTP 429) after multiple retries. "
                        "Check your quota and billing details."
                    ) from err
                suggested = extract_retry_after_seconds(err) or 0.0
                wait = max(suggested, backoff_seconds)
                time.sleep(wait)
                backoff_seconds = wait * 2
            except Exception as err:
                maybe_raise_credit_exhausted(self._provider_label, err)
                if emitted:
                    # Mid-stream failure: never retry — chunks are already on
                    # the user's screen and a retry would duplicate them.
                    raise
                if attempt == max_attempts - 1:
                    raise RuntimeError(
                        "LLM API request failed after multiple retries. Try again in a few seconds."
                    ) from err
                time.sleep(backoff_seconds)
                backoff_seconds *= 2

"""SDK-backed tool-calling LLM clients for the agent ReAct loop.

Supports Anthropic (native + Bedrock), OpenAI-compatible, and subprocess CLI providers.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable, Sequence
from typing import Any

from core.context_budget import strip_internal_message_markers
from core.llm.shared.llm_retry import (
    maybe_raise_credit_exhausted,
    rate_limit_sleep_seconds,
)
from core.llm.shared.openai_chat_completions import (
    _RETRY_INITIAL_BACKOFF_SEC,
    _RETRY_MAX_ATTEMPTS,
    AGENT_CLIENT_TIMEOUT_SEC,
)
from core.llm.shared.openai_chat_completions import (
    build_assistant_message as build_openai_compat_assistant_message,
)
from core.llm.shared.openai_chat_completions import (
    build_tool_result_messages as build_openai_compat_tool_result_messages,
)
from core.llm.shared.openai_responses import (
    response_raw_message,
    response_tool_calls,
    responses_input,
    responses_tool_specs,
    uses_responses_api,
)
from core.llm.shared.tool_schema_normalize import build_openai_tool_specs
from core.llm.shared.usage import emit_provider_usage, extract_cache_tokens
from core.llm.transports.sdk.anthropic_cache import (
    cached_system as _anthropic_cached_system,
)
from core.llm.transports.sdk.anthropic_cache import (
    is_cache_unsupported_error as _is_cache_unsupported_error,
)
from core.llm.transports.sdk.anthropic_cache import (
    messages_with_cache as _anthropic_messages_with_cache,
)
from core.llm.transports.sdk.anthropic_cache import (
    tools_with_cache as _anthropic_tools_with_cache,
)
from core.llm.types import AgentLLMResponse, ModelType, SchemaDescribedTool, ToolCall

logger = logging.getLogger(__name__)


def _anthropic_tool_schema(tool: Any) -> dict[str, Any]:
    return {
        "type": "custom",
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.public_input_schema,
    }


# Anthropic prompt-caching breakpoint (ephemeral, 5-minute TTL by default).
# Prefix match order is tools → system → messages; mark the last tool and the
# system block so ReAct iterations can reuse the stable prefix.
class AnthropicAgentClient:
    """Anthropic client with native tool-calling for the agent loop."""

    provider_name = "Anthropic"
    auth_error_hint = "Check ANTHROPIC_API_KEY."
    # Best-effort prompt caching: a class default so every instance starts
    # marking; flipped to an instance False for the client's lifetime the
    # first time the provider rejects the cache markers with a 400 (e.g.
    # Bedrock-hosted Claude models that predate prompt caching).
    _cache_markers_enabled = True

    def __init__(
        self,
        model: str,
        max_tokens: int = 4096,
        *,
        base_url: str | None = None,
        api_key_env: str = "ANTHROPIC_API_KEY",
        client: Any | None = None,
        credential_resolver: Callable[[str], str] | None = None,
    ) -> None:
        # base_url + api_key_env let a custom-anthropic gateway (Anthropic SDK
        # with a base-URL override) reuse this client. They default to the
        # first-party Anthropic endpoint/key, so existing behavior is unchanged.
        # Only override the hint for a non-default key env so subclasses
        # (e.g. BedrockAgentClient) keep their own class-level auth_error_hint.
        if api_key_env != "ANTHROPIC_API_KEY":
            self.auth_error_hint = f"Check {api_key_env}."
        if client is None:
            from anthropic import Anthropic

            from config.llm_credentials import resolve_env_credential

            resolver = credential_resolver or resolve_env_credential
            api_key = resolver(api_key_env)
            client_kwargs: dict[str, Any] = {
                "api_key": api_key,
                "timeout": AGENT_CLIENT_TIMEOUT_SEC,
            }
            if base_url:
                client_kwargs["base_url"] = base_url
            self._client = Anthropic(**client_kwargs)
        else:
            self._client = client
        self._model = model
        self._max_tokens = max_tokens

    @property
    def model_id(self) -> str | None:
        return self._model

    def tool_schemas(self, tools: Sequence[SchemaDescribedTool]) -> list[dict[str, Any]]:
        return [_anthropic_tool_schema(t) for t in tools]

    def describe_image(
        self,
        image_bytes: bytes,
        mimetype: str,
        *,
        prompt: str,
        max_tokens: int,
        timeout: float,
    ) -> str | None:
        """Return a text description of an image via this provider's vision model."""
        import base64

        messages: Any = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mimetype,
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        },
                    },
                ],
            }
        ]
        response = self._client.messages.create(
            model=self._model, max_tokens=max_tokens, timeout=timeout, messages=messages
        )
        parts = [
            block.text
            for block in getattr(response, "content", [])
            if getattr(block, "type", "") == "text" and getattr(block, "text", "")
        ]
        return "\n".join(parts).strip() or None

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        from anthropic import (
            AuthenticationError,
            BadRequestError,
            InternalServerError,
            NotFoundError,
            PermissionDeniedError,
            RateLimitError,
        )

        cache = self._cache_markers_enabled
        clean_messages = strip_internal_message_markers(messages)
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": _anthropic_messages_with_cache(clean_messages) if cache else clean_messages,
        }
        if system:
            kwargs["system"] = _anthropic_cached_system(system) if cache else system
        if tools:
            kwargs["tools"] = _anthropic_tools_with_cache(tools) if cache else tools

        backoff = _RETRY_INITIAL_BACKOFF_SEC
        last_err: Exception | None = None
        for attempt in range(_RETRY_MAX_ATTEMPTS):
            try:
                response = self._client.messages.create(**kwargs)
                break
            except AuthenticationError as err:
                raise RuntimeError(self._authentication_error_message()) from err
            except NotFoundError as err:
                raise RuntimeError(self._model_not_found_error_message()) from err
            except PermissionDeniedError as err:
                raise RuntimeError(self._permission_denied_error_message()) from err
            except BadRequestError as err:
                # Anthropic surfaces "credit balance too low" as HTTP 400;
                # distinguish credit exhaustion (fatal) from real schema
                # errors before wrapping into a generic RuntimeError.
                maybe_raise_credit_exhausted(self.provider_name, err)
                if cache and _is_cache_unsupported_error(err):
                    # Keyed on what *this* request carried, not the shared
                    # flag: a concurrent turn may already have cleared it, and
                    # this request still went out marked. Rebuilding with the
                    # flag off guards the recursion.
                    self._cache_markers_enabled = False
                    logger.warning(
                        "%s model '%s' rejected prompt-cache markers; retrying without "
                        "prompt caching (requests will pay full input price).",
                        self.provider_name,
                        self._model,
                    )
                    return self.invoke(messages, system=system, tools=tools)
                raise RuntimeError(self._bad_request_error_message(err)) from err
            except RateLimitError as err:
                # OpenAI's insufficient_quota lands here too, dressed as 429
                # with "rate limit" text. Distinguish billing exhaustion
                # (fatal — no retry will help) from transient TPM throttling.
                maybe_raise_credit_exhausted(self.provider_name, err)
                # Transient by definition — back off and retry instead of
                # failing the whole case. Honor ``Retry-After`` when the
                # provider sends one (much tighter than our jittered
                # default, which can be 10x longer than necessary).
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(
                        f"{self.provider_name} rate limit exceeded after "
                        f"{_RETRY_MAX_ATTEMPTS} attempts: {err}"
                    ) from err
                time.sleep(rate_limit_sleep_seconds(err, backoff))
                backoff *= 2
            except InternalServerError as err:
                body = getattr(err, "body", {}) or {}
                if (
                    isinstance(body, dict)
                    and isinstance(body.get("data"), dict)
                    and "model" in body["data"]
                ):
                    raise RuntimeError(
                        f"{self.provider_name} model '{self._model}' is not configured or billing is not enabled: "
                        f"{body.get('message', str(err))}"
                    ) from err
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(
                        f"{self.provider_name} API failed after {_RETRY_MAX_ATTEMPTS} attempts: {err}"
                    ) from err
                time.sleep(backoff)
                backoff *= 2
            except TypeError as err:
                # Anthropic SDK raises TypeError from _validate_headers when the API key is
                # missing or malformed — retrying won't fix a credential problem.
                if "could not resolve authentication" in str(err).lower():
                    raise RuntimeError(self._authentication_error_message()) from err
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(
                        f"{self.provider_name} API failed after {_RETRY_MAX_ATTEMPTS} attempts: {err}"
                    ) from err
                time.sleep(backoff)
                backoff *= 2
            except Exception as err:
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(
                        f"{self.provider_name} API failed after {_RETRY_MAX_ATTEMPTS} attempts: {err}"
                    ) from err
                time.sleep(backoff)
                backoff *= 2
        else:
            raise RuntimeError(f"{self.provider_name} invocation failed") from last_err

        content_blocks = getattr(response, "content", None)
        if not isinstance(content_blocks, list):
            logger.warning(
                "AnthropicAgentClient.invoke: unexpected response type %s — expected Message with .content list",
                type(response).__name__,
            )
            raise RuntimeError(
                f"{self.provider_name} API returned an unexpected response: {type(response).__name__}"
            )

        input_tokens, output_tokens = emit_provider_usage(
            self._model,
            getattr(response, "usage", None),
            input_key="input_tokens",
            output_key="output_tokens",
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in content_blocks:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(block.text)
            elif block_type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input)))

        cache_read, cache_write = extract_cache_tokens(getattr(response, "usage", None))
        return AgentLLMResponse(
            content="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=str(getattr(response, "stop_reason", "end_turn")),
            raw_content=content_blocks,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_write,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @staticmethod
    def build_tool_result_message(tool_calls: list[ToolCall], results: list[Any]) -> dict[str, Any]:
        """Build the Anthropic tool_result user message for one round of tool calls."""
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": json.dumps(result, default=str),
                }
                for tc, result in zip(tool_calls, results)
            ],
        }

    @staticmethod
    def build_assistant_message(raw_content: Any) -> dict[str, Any]:
        """Build the assistant message preserving full Anthropic content blocks."""
        return {"role": "assistant", "content": raw_content}

    def _authentication_error_message(self) -> str:
        return f"{self.provider_name} authentication failed. {self.auth_error_hint}"

    def _model_not_found_error_message(self) -> str:
        return f"{self.provider_name} model '{self._model}' not found."

    def _permission_denied_error_message(self) -> str:
        return f"{self.provider_name} API access denied. Check your API key permissions."

    def _bad_request_error_message(self, err: Any) -> str:
        return f"{self.provider_name} request rejected (HTTP 400): {err.message}"


class BedrockAgentClient(AnthropicAgentClient):
    """Bedrock-backed client using AnthropicBedrock SDK."""

    provider_name = "Bedrock"
    auth_error_hint = (
        "Check AWS credentials (for example AWS_PROFILE, AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, "
        "or instance role) and AWS_REGION/AWS_DEFAULT_REGION."
    )

    def __init__(self, model: str, max_tokens: int = 4096) -> None:
        from anthropic import AnthropicBedrock

        region = (os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "").strip()
        if not region:
            raise RuntimeError("Bedrock requires AWS_REGION or AWS_DEFAULT_REGION to be set.")

        bedrock_client = AnthropicBedrock(
            aws_region=region,
            timeout=AGENT_CLIENT_TIMEOUT_SEC,
        )
        super().__init__(model=model, max_tokens=max_tokens, client=bedrock_client)

    def _permission_denied_error_message(self) -> str:
        return (
            f"Bedrock model '{self._model}' is not available for your account. "
            "Check Bedrock model access in the configured AWS region, AWS Marketplace "
            "subscription/payment setup, and IAM permissions including "
            "aws-marketplace:ViewSubscriptions and aws-marketplace:Subscribe."
        )

    def _bad_request_error_message(self, err: Any) -> str:
        err_str = str(err)
        if "on-demand throughput" in err_str or "inference profile" in err_str.lower():
            return (
                f"Bedrock model '{self._model}' requires a cross-region inference profile. "
                f"Try prefixing with 'us.' (e.g. 'us.{self._model}') and update "
                "BEDROCK_REASONING_MODEL or BEDROCK_TOOLCALL_MODEL."
            )
        return f"{self.provider_name} request rejected (HTTP 400): {err.message}"


class BedrockConverseAgentClient:
    """Bedrock tool-calling client using the boto3 Converse API (non-Anthropic models)."""

    provider_name = "Bedrock"

    def __init__(self, model: str, max_tokens: int = 4096) -> None:
        import boto3

        from core.llm.transports.sdk.bedrock_converse import require_aws_region

        self._model = model
        self._max_tokens = max_tokens
        region = require_aws_region()
        self._boto3_client = boto3.client("bedrock-runtime", region_name=region)

    @property
    def model_id(self) -> str | None:
        return self._model

    def tool_schemas(self, tools: Sequence[SchemaDescribedTool]) -> list[dict[str, Any]]:
        from core.llm.transports.sdk.bedrock_converse import build_converse_tool_specs

        return build_converse_tool_specs(tools)

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        import botocore.exceptions

        from core.llm.transports.sdk.bedrock_converse import (
            is_non_retryable_bedrock_code,
            map_bedrock_client_error,
            parse_converse_output,
            to_converse_messages,
        )
        from infrastructure.safety.guardrails.apply import apply_guardrails_to_converse_payload
        from infrastructure.safety.guardrails.evaluator import GuardrailBlockedError

        converse_messages = to_converse_messages(strip_internal_message_markers(messages))
        converse_messages, system = apply_guardrails_to_converse_payload(
            messages=converse_messages,
            system=system,
        )

        kwargs: dict[str, Any] = {
            "modelId": self._model,
            "inferenceConfig": {"maxTokens": self._max_tokens},
            "messages": converse_messages,
        }
        if system:
            kwargs["system"] = [{"text": system}]
        if tools:
            kwargs["toolConfig"] = {"tools": tools}

        backoff = _RETRY_INITIAL_BACKOFF_SEC
        response: dict[str, Any] | None = None
        last_err: Exception | None = None
        for attempt in range(_RETRY_MAX_ATTEMPTS):
            try:
                response = self._boto3_client.converse(**kwargs)
                break
            except GuardrailBlockedError:
                raise
            except botocore.exceptions.ClientError as err:
                code = err.response.get("Error", {}).get("Code", "")
                if is_non_retryable_bedrock_code(code):
                    raise map_bedrock_client_error(self._model, err) from err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise map_bedrock_client_error(self._model, err) from err
                last_err = err
                time.sleep(backoff)
                backoff *= 2
            except Exception as err:
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(f"Bedrock API request failed: {err}") from err
                last_err = err
                time.sleep(backoff)
                backoff *= 2
        else:
            raise RuntimeError("Bedrock invocation failed without a concrete error") from last_err

        if response is None:
            raise RuntimeError("Bedrock invocation failed without a response") from last_err

        input_tokens, output_tokens = emit_provider_usage(
            self._model,
            response.get("usage"),
            input_key="inputTokens",
            output_key="outputTokens",
        )

        content, raw_tool_calls, stop_reason, raw_message = parse_converse_output(response)
        tool_calls = [
            ToolCall(id=tool_id, name=name, input=inputs)
            for tool_id, name, inputs in raw_tool_calls
        ]
        return AgentLLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            raw_content=raw_message,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @staticmethod
    def build_tool_result_message(tool_calls: list[ToolCall], results: list[Any]) -> dict[str, Any]:
        from core.llm.transports.sdk.bedrock_converse import build_tool_result_message as _build

        return _build(tool_calls, results)

    @staticmethod
    def build_assistant_message(raw_content: Any) -> dict[str, Any]:
        """Preserve the full Converse assistant ``output.message`` for the next turn."""
        if not isinstance(raw_content, dict):
            return {"role": "assistant", "content": []}
        return raw_content


_OPENAI_O_SERIES_RE = re.compile(r"(?:^|[^A-Za-z0-9])o\d", re.IGNORECASE)
_OPENAI_GPT5_RE = re.compile(r"(?:^|[^A-Za-z0-9])gpt-5", re.IGNORECASE)


def _supports_openai_parallel_tool_calls_param(api_key_env: str) -> bool:
    """Return whether this client targets OpenAI's native chat-completions API."""
    return api_key_env == "OPENAI_API_KEY"


def _openai_max_token_kwarg(model: str) -> str:
    # OpenAI o-series (o1, o3, o4-mini, …) and gpt-5 series reject max_tokens.
    # O-series: matches a bare ``o<digit>`` token at the start of the name or
    # following a non-alphanumeric separator, so vendor-prefixed routes
    # (``openai/o4-mini``, ``azure/o3``) and custom deployments are detected.
    # gpt-5: matches ``gpt-5`` at the start or after a separator, covering
    # gpt-5, gpt-5o, gpt-5o-mini, and future gpt-5* variants.
    if _OPENAI_O_SERIES_RE.search(model) or _OPENAI_GPT5_RE.search(model):
        return "max_completion_tokens"
    return "max_tokens"


# .title() breaks brand names with an internal capital letter (e.g.
# "OPENAI_API_KEY" -> "Openai" instead of "OpenAI"). Override just the
# providers this class is actually constructed with (see
# core/llm/providers/openai_compat_providers.py) where that happens.
_PROVIDER_LABEL_OVERRIDES = {
    "OPENAI_API_KEY": "OpenAI",
    "OPENROUTER_API_KEY": "OpenRouter",
    "TRUSTEDROUTER_API_KEY": "TrustedRouter",
    "MINIMAX_API_KEY": "MiniMax",
}


class OpenAIAgentClient:
    """OpenAI-compatible client with tool-calling for the agent loop."""

    def __init__(
        self,
        model: str,
        max_tokens: int = 4096,
        base_url: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        api_key_default: str = "",
        credential_resolver: Callable[[str], str] | None = None,
    ) -> None:
        from openai import OpenAI

        from config.llm_credentials import resolve_env_credential

        resolver = credential_resolver or resolve_env_credential
        api_key = resolver(api_key_env) or api_key_default
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=AGENT_CLIENT_TIMEOUT_SEC)
        self._model = model
        self._max_tokens = max_tokens
        self._api_key_env = api_key_env

    @property
    def model_id(self) -> str | None:
        return self._model

    @property
    def _provider_label(self) -> str:
        api_key_env = str(getattr(self, "_api_key_env", "OPENAI_API_KEY"))
        override = _PROVIDER_LABEL_OVERRIDES.get(api_key_env)
        if override:
            return override
        return api_key_env.removesuffix("_API_KEY").replace("_", " ").title()

    def tool_schemas(self, tools: Sequence[SchemaDescribedTool]) -> list[dict[str, Any]]:
        return build_openai_tool_specs(tools)

    def describe_image(
        self,
        image_bytes: bytes,
        mimetype: str,
        *,
        prompt: str,
        max_tokens: int,
        timeout: float,
    ) -> str | None:
        """Return a text description of an image via this provider's vision model."""
        import base64

        data_url = f"data:{mimetype};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        messages: Any = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
        response = self._client.chat.completions.create(
            model=self._model, max_tokens=max_tokens, timeout=timeout, messages=messages
        )
        choices = getattr(response, "choices", [])
        if not choices:
            return None
        content = getattr(choices[0].message, "content", "") or ""
        return content.strip() or None

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        from openai import (
            AuthenticationError,
            BadRequestError,
            NotFoundError,
            PermissionDeniedError,
            RateLimitError,
        )

        msgs = strip_internal_message_markers(messages)
        if system:
            msgs = [{"role": "system", "content": system}] + msgs

        api_key_env = str(getattr(self, "_api_key_env", "OPENAI_API_KEY"))
        use_responses = uses_responses_api(self._model, api_key_env)
        if use_responses:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "max_output_tokens": self._max_tokens,
                "input": responses_input(msgs),
            }
            if tools:
                kwargs["tools"] = responses_tool_specs(tools)
                kwargs["tool_choice"] = "auto"
                kwargs["parallel_tool_calls"] = True
            from config.llm_reasoning_effort import get_active_reasoning_effort

            reasoning_effort = get_active_reasoning_effort()
            if reasoning_effort is not None:
                kwargs["reasoning"] = {"effort": reasoning_effort}
        else:
            kwargs = {
                "model": self._model,
                _openai_max_token_kwarg(self._model): self._max_tokens,
                "messages": msgs,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
                if _supports_openai_parallel_tool_calls_param(api_key_env):
                    kwargs["parallel_tool_calls"] = True

        backoff = _RETRY_INITIAL_BACKOFF_SEC
        last_err: Exception | None = None
        for attempt in range(_RETRY_MAX_ATTEMPTS):
            try:
                if use_responses:
                    response = self._client.responses.create(**kwargs)
                else:
                    response = self._client.chat.completions.create(**kwargs)
                break
            except AuthenticationError as err:
                raise RuntimeError(f"{self._provider_label} authentication failed.") from err
            except NotFoundError as err:
                raise RuntimeError(
                    f"{self._provider_label} model '{self._model}' not found."
                ) from err
            except BadRequestError as err:
                # Some providers (or proxies) surface insufficient_quota as
                # 400 — distinguish before the generic wrap.
                maybe_raise_credit_exhausted(self._provider_label, err)
                raise RuntimeError(f"{self._provider_label} request rejected: {err}") from err
            except RateLimitError as err:
                # OpenAI returns insufficient_quota as HTTP 429 with body
                # text "You exceeded your current quota". Halt rather than
                # burning retries on a dead account.
                maybe_raise_credit_exhausted(self._provider_label, err)
                # Transient by definition — back off and retry instead of
                # failing the whole cell. OpenAI's body usually carries a
                # tight hint like ``"Please try again in 94ms"``; honor it
                # when present instead of waiting our deterministic backoff
                # (matters most on tight tiers like gpt-4o's 30k TPM).
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(
                        f"{self._provider_label} rate limit exceeded after "
                        f"{_RETRY_MAX_ATTEMPTS} attempts: {err}"
                    ) from err
                time.sleep(rate_limit_sleep_seconds(err, backoff))
                backoff *= 2
            except PermissionDeniedError as err:
                raise RuntimeError(f"{self._provider_label} request forbidden: {err}") from err
            except Exception as err:
                maybe_raise_credit_exhausted(self._provider_label, err)
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(f"{self._provider_label} API failed: {err}") from err
                time.sleep(backoff)
                backoff *= 2
        else:
            raise RuntimeError(f"{self._provider_label} invocation failed") from last_err

        if use_responses:
            input_tokens, output_tokens = emit_provider_usage(
                self._model,
                getattr(response, "usage", None),
                input_key="input_tokens",
                output_key="output_tokens",
            )
            responses_tool_calls = response_tool_calls(response)
            return AgentLLMResponse(
                content=str(getattr(response, "output_text", "") or ""),
                tool_calls=responses_tool_calls,
                stop_reason="tool_calls" if responses_tool_calls else "stop",
                raw_content=response_raw_message(response),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        if not hasattr(response, "choices") or not response.choices:
            raise RuntimeError(
                f"{self._provider_label} API returned an unexpected response: "
                f"{type(response).__name__}"
            )
        input_tokens, output_tokens = emit_provider_usage(
            self._model,
            getattr(response, "usage", None),
            input_key="prompt_tokens",
            output_key="completion_tokens",
        )
        choice = response.choices[0]
        msg = choice.message
        content = msg.content or ""
        stop_reason = choice.finish_reason or "stop"

        tool_calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            try:
                input_dict = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                input_dict = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=input_dict))

        return AgentLLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            # Preserve the raw API message so provider-specific fields (e.g. Gemini
            # thought_signature in tool_calls) survive into the next conversation turn.
            # exclude_none=True strips null fields (refusal, audio, function_call …)
            # that strict OpenAI-compatible endpoints may reject on replay.
            raw_content=msg.model_dump(exclude_none=True),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @staticmethod
    def build_tool_result_message(tool_calls: list[ToolCall], results: list[Any]) -> dict[str, Any]:
        raise NotImplementedError("OpenAI tool results must be appended as separate messages")

    build_tool_result_messages = staticmethod(build_openai_compat_tool_result_messages)
    build_assistant_message = staticmethod(build_openai_compat_assistant_message)


class CLIBackedAgentClient:
    """Tool-calling wrapper for subprocess CLI providers (codex, claude-code, etc.).

    CLI adapters don't expose a native tool-calling API. This client implements
    the agent's ReAct interface by embedding tool schemas in the
    prompt as JSON and parsing the model's text response for tool call JSON.
    Each invoke flattens the full conversation history into a single stdin prompt.
    """

    _TOOL_CALL_INSTRUCTION = (
        "You are a tool-calling adapter for the current OpenSRE phase. Follow the "
        "System instructions and choose the appropriate available tool. On each "
        "turn respond with EITHER:\n"
        '  (a) A JSON object: {"tool_calls": [{"id": "<unique_id>", "name": "<tool>",'
        ' "input": {<args>}}]}\n'
        "  (b) A concise plain-text final answer only after tool use is complete "
        "or when no suitable tool exists.\n"
        "Respond with JSON only when calling tools; respond with plain text only "
        "for the final answer."
    )

    def __init__(self, adapter: Any, *, model: str | None = None) -> None:
        from infrastructure.harness_providers import build_cli_client

        self._adapter = adapter
        self._model = model
        # Reuse one client so any probe cache in the CLI backend applies across
        # ReAct iterations instead of re-probing every invoke.
        self._cli_client = build_cli_client(
            adapter,
            model=self._model,
            model_type=ModelType.TOOLCALL,
        )

    @property
    def model_id(self) -> str | None:
        return self._model

    def tool_schemas(self, tools: Sequence[SchemaDescribedTool]) -> list[dict[str, Any]]:
        # ``public_input_schema``, not ``input_schema``: injected parameters
        # (credentials, endpoints) are pruned from what the model is shown. The
        # other clients already did this; this one did not, so on a CLI-backed
        # model a tool advertised its own ``github_token`` as fillable.
        return [
            {"name": t.name, "description": t.description, "input_schema": t.public_input_schema}
            for t in tools
        ]

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        from infrastructure.harness_providers import flatten_cli_messages_to_prompt

        tool_block = ""
        if tools:
            tool_lines = json.dumps(tools, indent=2)
            tool_block = f"\n\nAvailable tools (JSON schema):\n{tool_lines}\n"

        system_block = f"System: {system}\n" if system else ""
        instruction = self._TOOL_CALL_INSTRUCTION + tool_block
        prompt = f"{system_block}{instruction}\n\n{flatten_cli_messages_to_prompt(messages)}"

        text = self._cli_client.invoke(prompt).content.strip()

        # Try to parse a JSON tool call response.
        tool_calls: list[ToolCall] = []
        parsed_json = _try_parse_tool_call_json(text)
        if parsed_json is not None:
            raw_calls = parsed_json.get("tool_calls")
            if isinstance(raw_calls, list):
                for i, tc in enumerate(raw_calls):
                    if not isinstance(tc, dict):
                        continue
                    name = tc.get("name")
                    if not isinstance(name, str) or not name.strip():
                        continue
                    raw_input = tc.get("input")
                    input_payload = raw_input if isinstance(raw_input, dict) else {}
                    tool_calls.append(
                        ToolCall(
                            id=str(tc.get("id") or f"call_{i}"),
                            name=name.strip(),
                            input=input_payload,
                        )
                    )
            content = "" if tool_calls else text
        else:
            content = text

        return AgentLLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
            raw_content=None,  # None so _build_assistant_msg falls through to build_assistant_message
        )

    @staticmethod
    def build_tool_result_message(tool_calls: list[ToolCall], results: list[Any]) -> dict[str, Any]:
        parts = [
            f"Tool result for {tc.name} (id={tc.id}): {json.dumps(result, default=str)}"
            for tc, result in zip(tool_calls, results)
        ]
        return {"role": "user", "content": "\n".join(parts)}

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        """Match OpenAIAgentClient signature; embed tool JSON in content for CLI history."""
        if tool_calls:
            payload = {
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "input": tc.input} for tc in tool_calls
                ]
            }
            tool_json = json.dumps(payload)
            if content.strip():
                return {"role": "assistant", "content": f"{content.strip()}\n\n{tool_json}"}
            return {"role": "assistant", "content": tool_json}
        return {"role": "assistant", "content": content}


def _try_parse_tool_call_json(text: str) -> dict[str, Any] | None:
    """Return parsed JSON dict if *text* contains a tool_calls JSON object.

    Uses :meth:`json.JSONDecoder.raw_decode` so a single JSON value is parsed
    without a greedy ``{...}`` span swallowing trailing brace-containing prose and
    breaking :func:`json.loads`.
    """
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
    candidate = (fence.group(1).strip() if fence else cleaned).strip()
    if not candidate:
        return None

    decoder = json.JSONDecoder()

    def _decode_at(idx: int) -> dict[str, Any] | None:
        try:
            payload, _end = decoder.raw_decode(candidate, idx)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict) and "tool_calls" in payload:
            return payload
        return None

    # Fast path: candidate is pure JSON (the preferred model behavior).
    parsed = _decode_at(0)
    if parsed is not None:
        return parsed

    # Recovery path: some CLI models prepend unfenced prose before the JSON
    # object. Scan object starts and accept the first dict that contains
    # "tool_calls". Skip index 0 because the fast path already attempted it.
    for match in re.finditer(r"\{", candidate):
        if match.start() == 0:
            continue
        parsed = _decode_at(match.start())
        if parsed is not None:
            return parsed

    return None

"""Classify LLM invoke failures for CLI error mapping."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class LLMInvokeFailure:
    """User-facing failure derived from an LLM invoke exception."""

    user_message: str
    tracker_message: str
    remediation_steps: list[str]


def _timeout_remediation() -> list[str]:
    return [
        (
            "CLI providers: raise the per-provider timeout env "
            + "(e.g. GEMINI_CLI_TIMEOUT_SECONDS, CLAUDE_CODE_TIMEOUT_SECONDS, "
            + "ANTIGRAVITY_CLI_TIMEOUT_SECONDS; clamped 30–600 where supported)."
        ),
        (
            "API providers (Anthropic, OpenAI, etc.): each ReAct turn is limited to "
            + "~90s per HTTP request; retry or switch to a faster model if turns time out."
        ),
        "Agent turns run many LLM and tool steps — total wall time can be several minutes.",
    ]


_TIMEOUT_EXCEPTION_NAMES = frozenset(
    {
        "APITimeoutError",
        "ConnectTimeout",
        "PoolTimeout",
        "ReadTimeout",
        "TimeoutException",
        "WriteTimeout",
    }
)


def _looks_like_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if type(exc).__name__ in _TIMEOUT_EXCEPTION_NAMES:
        return True
    try:
        from anthropic import APITimeoutError as AnthropicTimeoutError
    except ImportError:
        pass
    else:
        if isinstance(exc, AnthropicTimeoutError):
            return True

    try:
        from openai import APITimeoutError as OpenAITimeoutError
    except ImportError:
        pass
    else:
        if isinstance(exc, OpenAITimeoutError):
            return True

    text = str(exc).lower()
    if "timed out" in text or "timeout" in text:
        return True
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, TimeoutError):
            return True
        next_cause = cause.__cause__ or cause.__context__
        if next_cause is cause:
            break
        cause = next_cause if isinstance(next_cause, BaseException) else None
    return False


def _is_llm_cli_error(exc: BaseException, class_name: str) -> bool:
    """True when *exc* is ``integrations.llm_cli.errors.<class_name>`` (matched by name)."""
    exc_type = type(exc)
    return exc_type.__name__ == class_name and exc_type.__module__.endswith(
        "integrations.llm_cli.errors"
    )


def is_cli_timeout_error(exc: BaseException) -> bool:
    """Return True when *exc* is a CLI subprocess timeout (expected on slow turns)."""
    return _is_llm_cli_error(exc, "CLITimeoutError")


# Turn-error kinds staged when the conversational/action LLM was the intended
# route for the user's input but the provider failed before a normal reply.
# The prompt-log recorder uses this set to report a failed LLM turn (model
# "unknown" plus ``ai_error_kind``) instead of a terminal-action turn
# (``no_conversational_agent``). Terminal-path kinds (background-task
# "timeout"/"cli_exit_nonzero", slash outcomes) must never appear here.
ACTION_AGENT_ERROR = "action_agent_error"  # action-selection LLM failed for conversational input
LLM_PROVIDER_FAILURE_KINDS = frozenset(
    {
        "llm_unavailable",  # reasoning client import/creation failed
        "llm_timeout",  # conversational stream timed out
        "assistant_error",  # conversational stream failed mid-turn
        ACTION_AGENT_ERROR,
    }
)


class ProviderFailureKind(StrEnum):
    """One verdict per provider failure message; every consumer derives from it."""

    MISSING_KEY = "missing_key"
    MISSING_ENDPOINT = "missing_endpoint"
    REJECTED_KEY = "rejected_key"
    QUOTA = "quota"
    NOT_CONFIGURED = "not_configured"
    PROVIDER_ERROR = "provider_error"


# First match wins, most specific first. MISSING_KEY holds absence phrasings
# only — never a bare env-var-name match: rejected-key errors also cite
# *_API_KEY names and must keep their real authentication message. A bare
# "to be set" is not a key absence either: the settings validator also says it
# about base URLs and AWS regions.
_FAILURE_RULES: tuple[tuple[ProviderFailureKind, tuple[str, ...]], ...] = (
    (
        ProviderFailureKind.MISSING_KEY,
        (
            "missing credentials",
            "api key is not set",
            "missing api key",
            "no api key",
            "could not resolve authentication method",  # anthropic SDK, key/token both unset
            "api_key to be set",  # opensre wrapper: "requires ANTHROPIC_API_KEY to be set"
        ),
    ),
    (
        ProviderFailureKind.MISSING_ENDPOINT,
        ("base_url to be set",),  # settings: "requires CUSTOM_OPENAI_BASE_URL to be set"
    ),
    (
        ProviderFailureKind.REJECTED_KEY,
        (
            "invalid",
            "incorrect",
            "expired",
            "revoked",
            "401",
            "403",
            "unauthorized",
            "forbidden",
            "authentication",
            "x-api-key",
        ),
    ),
    (
        ProviderFailureKind.QUOTA,
        ("429", "quota", "rate limit", "too many requests", "credit"),
    ),
    (
        ProviderFailureKind.NOT_CONFIGURED,
        (
            "_api_key",  # env-var style, e.g. "set the OPENAI_API_KEY environment variable"
            "not available for your account",
            "marketplace",
            "inference profile",
            "not configured",
            "no llm provider",
            "llm client unavailable",
            "billing is not enabled",
            "to be set",  # any other required setting, e.g. "requires AWS_REGION ... to be set"
        ),
    ),
)


def classify_llm_provider_failure(message: str) -> ProviderFailureKind:
    """Classify a provider failure message once; rendering and analytics both read this."""
    text = message.lower()
    for kind, patterns in _FAILURE_RULES:
        if any(pattern in text for pattern in patterns):
            return kind
    if "model" in text and "not found" in text:
        return ProviderFailureKind.NOT_CONFIGURED
    return ProviderFailureKind.PROVIDER_ERROR


class CommandSurface(StrEnum):
    """Where the reader runs OpenSRE commands, which decides how guidance spells them."""

    #: Slash commands typed at the interactive shell prompt.
    SHELL = "shell"
    #: ``opensre`` subcommands run from a terminal, script or CI job.
    CLI = "cli"


_AUTH_LOGIN_COMMAND: dict[CommandSurface, str] = {
    CommandSurface.SHELL: "/auth login",
    CommandSurface.CLI: "opensre auth login",
}
_ONBOARD_COMMAND: dict[CommandSurface, str] = {
    CommandSurface.SHELL: "/onboard",
    CommandSurface.CLI: "opensre onboard",
}
_ACCOUNT_LOGIN_COMMAND: dict[CommandSurface, str] = {
    CommandSurface.SHELL: "/account login",
    CommandSurface.CLI: "opensre account login",
}


def remediate_llm_setup_failure(
    message: str,
    *,
    surface: CommandSurface,
    provider: str | None = None,
    offer_account_login: bool = False,
) -> str | None:
    """Setup guidance that replaces *message* when an LLM call failed for missing setup.

    A missing API key or endpoint base URL gets the commands that fix it, spelled
    for *surface*. *provider* is named only as the catalog id those commands
    accept. *offer_account_login* adds the OpenSRE-hosted model as an alternative
    for a signed-out reader. Returns ``None`` for every other failure (rejected
    key, quota, timeout, …) so callers fall back to their existing rendering.
    """
    kind = classify_llm_provider_failure(message)
    if kind is ProviderFailureKind.MISSING_KEY:
        guidance = _missing_key_guidance(provider, surface)
    elif kind is ProviderFailureKind.MISSING_ENDPOINT:
        guidance = _missing_endpoint_guidance(provider, surface)
    else:
        return None
    if not offer_account_login:
        return guidance
    return (
        f"{guidance} To use the OpenSRE-hosted model instead, sign in with "
        f"`{_ACCOUNT_LOGIN_COMMAND[surface]}`."
    )


def _missing_key_guidance(provider: str | None, surface: CommandSurface) -> str:
    from config.llm_auth.provider_catalog import provider_spec

    spec = provider_spec(provider) if provider else None
    # Only an OpenSRE-managed API-key provider has an ``auth login`` profile.
    target = spec.value if spec is not None and spec.uses_open_sre_api_key else None
    subject = f"No API key is set for {target}" if target else "No LLM API key is set"
    return (
        f"{subject}. Run `{_AUTH_LOGIN_COMMAND[surface]} {target or '<provider>'}` "
        f"to add one, or `{_ONBOARD_COMMAND[surface]}` to rerun setup."
    )


def _missing_endpoint_guidance(provider: str | None, surface: CommandSurface) -> str:
    from config.llm_auth.provider_catalog import provider_spec

    spec = provider_spec(provider) if provider else None
    subject = (
        f"{spec.endpoint_env} is not set for {spec.value}"
        if spec is not None and spec.endpoint_env
        else "No LLM endpoint base URL is set"
    )
    return f"{subject}. Run `{_ONBOARD_COMMAND[surface]}` to configure the endpoint."


# Analytics vocabulary predates the split of key failures into missing vs
# rejected; dashboards filter on these four values.
_ANALYTICS_KIND_BY_FAILURE: dict[ProviderFailureKind, str] = {
    ProviderFailureKind.MISSING_KEY: "not_configured",
    ProviderFailureKind.MISSING_ENDPOINT: "not_configured",
    ProviderFailureKind.REJECTED_KEY: "auth",
    ProviderFailureKind.QUOTA: "quota",
    ProviderFailureKind.NOT_CONFIGURED: "not_configured",
    ProviderFailureKind.PROVIDER_ERROR: "provider_error",
}


def classify_provider_error_kind(message: str) -> str:
    """Bucket an LLM provider failure message for analytics filtering.

    Returns one of ``not_configured``, ``quota``, ``auth``, or
    ``provider_error`` so downstream dashboards can filter provider failures
    without regexing over response text.
    """
    return _ANALYTICS_KIND_BY_FAILURE[classify_llm_provider_failure(message)]


def classify_llm_invoke_failure(exc: BaseException) -> LLMInvokeFailure | None:
    """Return a structured failure when *exc* is a known operational LLM error.

    Returns ``None`` to signal the caller should re-raise. In particular,
    :class:`LLMCreditExhaustedError` is intentionally NOT classified — it
    represents a non-recoverable billing condition that callers must halt
    on, not wrap into a degraded result.
    """
    from core.llm.shared.llm_retry import LLMCreditExhaustedError

    # Fatal — propagate to the runner / operator. Do NOT wrap into the
    # generic "rate-limited" classification (which the text branch below
    # would otherwise match against "credit balance too low" / "quota").
    if isinstance(exc, LLMCreditExhaustedError):
        return None

    if _is_llm_cli_error(exc, "CLIAuthenticationRequired"):
        provider = getattr(exc, "provider", None) or "unknown"
        return LLMInvokeFailure(
            user_message=(
                f"The {provider} CLI is not authenticated, so the agent could not call the model."
            ),
            tracker_message="Failed: CLI not authenticated",
            remediation_steps=[
                step
                for step in (
                    getattr(exc, "auth_hint", None),
                    getattr(exc, "detail", None),
                    "Run `opensre doctor` to verify CLI installation and auth.",
                )
                if step
            ],
        )

    if is_cli_timeout_error(exc):
        detail = str(exc).strip() or "The CLI subprocess exceeded its time limit."
        return LLMInvokeFailure(
            user_message=f"LLM call stopped: {detail}",
            tracker_message="Failed: LLM timed out",
            remediation_steps=_timeout_remediation(),
        )

    if _is_llm_cli_error(exc, "CLIInterruptedError"):
        return LLMInvokeFailure(
            user_message="The turn was interrupted while waiting for the LLM CLI.",
            tracker_message="Failed: LLM interrupted",
            remediation_steps=["Retry when ready."],
        )

    if not isinstance(exc, RuntimeError):
        if _looks_like_timeout(exc):
            return LLMInvokeFailure(
                user_message="LLM call stopped: the LLM request timed out.",
                tracker_message="Failed: LLM timed out",
                remediation_steps=_timeout_remediation(),
            )
        return None

    err_msg = str(exc).lower()
    raw = str(exc)

    if ("model" in err_msg and "not found" in err_msg) or "404" in err_msg:
        from core.llm.providers.azure_openai import (
            azure_deployment_not_found_remediation_steps,
            is_azure_openai_failure_message,
        )

        if "anthropic" in err_msg and "was not found" in err_msg:
            return LLMInvokeFailure(
                user_message="Anthropic model was not found. Check your configured model name.",
                tracker_message="Failed: Model not found",
                remediation_steps=[
                    (
                        "Verify your model name in ANTHROPIC_REASONING_MODEL or "
                        + "ANTHROPIC_TOOLCALL_MODEL environment variables."
                    ),
                    "Confirm the model ID is valid for your Anthropic account.",
                ],
            )
        if "azure openai deployment" in err_msg or is_azure_openai_failure_message(raw):
            return LLMInvokeFailure(
                user_message="The configured Azure OpenAI deployment was not found (404).",
                tracker_message="Failed: Azure deployment not found",
                remediation_steps=azure_deployment_not_found_remediation_steps(),
            )
        return LLMInvokeFailure(
            user_message=(
                "The configured AI model was not found (404). "
                "If using a local LLM, verify the model name in your .env file."
            ),
            tracker_message="Failed: Model not found",
            remediation_steps=[
                "Check your .env configuration",
                "Verify the model name is correct",
                "Ensure the model is downloaded locally",
                "Confirm your provider supports this model",
            ],
        )

    if "does not support tool" in err_msg or "only supports single tool" in err_msg:
        return LLMInvokeFailure(
            user_message=(
                "The configured model does not support tool calling. "
                "The agent requires a model with native tool-calling support."
            ),
            tracker_message="Failed: Model does not support tools",
            remediation_steps=[
                "Switch to a model that supports tool calling (e.g. claude-opus-4-7, gpt-4o)",
                "For Ollama: use llama3.1, qwen2.5, or another tool-call-capable model",
                "Check your LLM_MODEL or LLM_PROVIDER setting in .env",
            ],
        )

    if "rate limit" in err_msg:
        return LLMInvokeFailure(
            user_message="The LLM provider rate-limited this request.",
            tracker_message="Failed: LLM rate limited",
            remediation_steps=[
                "Wait a few minutes and retry.",
                "Reduce parallel load or switch to a higher quota tier if available.",
            ],
        )

    if (
        "not authenticated" in err_msg
        or "authentication" in err_msg
        or ("api key" in err_msg and "invalid" in err_msg)
    ):
        return LLMInvokeFailure(
            user_message="LLM call stopped: LLM authentication failed.",
            tracker_message="Failed: LLM authentication",
            remediation_steps=[
                "Verify API keys or CLI login for your LLM_PROVIDER.",
                "Run `opensre doctor` to check provider configuration.",
            ],
        )

    if _looks_like_timeout(exc):
        return LLMInvokeFailure(
            user_message="LLM call stopped: the LLM request timed out.",
            tracker_message="Failed: LLM timed out",
            remediation_steps=_timeout_remediation(),
        )

    return None

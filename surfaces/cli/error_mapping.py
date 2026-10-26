"""Map low-level CLI runtime errors to user-facing CLI errors."""

from __future__ import annotations

from typing import NoReturn


def reraise_cli_runtime_error(exc: BaseException) -> NoReturn:
    """Convert CLI auth/setup failures to structured CLI UX errors."""
    from core.llm.shared.llm_retry import (
        LLMCreditExhaustedError,
        OpenSRECreditsExhaustedError,
    )
    from core.llm_invoke_errors import classify_llm_invoke_failure
    from integrations.llm_cli import CLIAuthenticationRequired
    from surfaces.shared.error_handling.errors import OpenSREError

    if isinstance(exc, OpenSRECreditsExhaustedError):
        destination = exc.upgrade_url or "the OpenSRE Usage page"
        raise OpenSREError(
            str(exc),
            suggestion=f"Upgrade your plan or buy a credit top-up at {destination}.",
        ) from exc

    if isinstance(exc, LLMCreditExhaustedError):
        raise OpenSREError(
            str(exc),
            suggestion=(
                "Run `opensre auth login <provider>` to re-authenticate "
                "or switch to a different provider."
            ),
        ) from exc

    if isinstance(exc, CLIAuthenticationRequired):
        raise OpenSREError(
            f"{exc.provider} CLI is not authenticated.",
            suggestion=f"{exc.auth_hint} ({exc.detail})",
        ) from exc

    classified = classify_llm_invoke_failure(exc)
    if classified is not None:
        suggestion = (
            "\n".join(classified.remediation_steps) if classified.remediation_steps else None
        )
        raise OpenSREError(classified.user_message, suggestion=suggestion) from exc

    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        if "cli not found" in msg or "not found on path" in msg:
            raise OpenSREError(
                "CLI tool is not installed or not found.",
                suggestion=str(exc),
            ) from exc
        if (
            "prompt too long" in msg
            and "auth status could not be verified before invocation" in msg
        ):
            raise OpenSREError(
                "LLM invocation failed.",
                suggestion=str(exc),
            ) from exc
        if "anthropic" in msg and "model" in msg and "was not found" in msg:
            raise OpenSREError(
                str(exc),
                suggestion="Verify your model name in ANTHROPIC_REASONING_MODEL or ANTHROPIC_TOOLCALL_MODEL environment variables.",
            ) from exc
        if "bedrock model" in msg and "not available for your account" in msg:
            raise OpenSREError(
                str(exc),
                suggestion=(
                    "Enable access to the configured Bedrock model in the AWS region, "
                    "verify the AWS Marketplace subscription/payment setup, and ensure "
                    "the IAM user or role can use aws-marketplace:ViewSubscriptions "
                    "and aws-marketplace:Subscribe."
                ),
            ) from exc

    raise exc

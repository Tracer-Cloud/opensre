"""Provider-agnostic rate-limit retries and billing/quota error detection."""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable

from config.constants.analytics import LLMCreditErrorReason

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_INITIAL_BACKOFF_SEC = 2.0
RETRY_AFTER_MAX_SEC = 30.0

_BODY_RETRY_HINT_RE = re.compile(
    r"(?:try again in|retry in) (\d+(?:\.\d+)?)\s*(ms|s)\b", re.IGNORECASE
)

_RATE_LIMIT_HINTS: tuple[str, ...] = (
    "rate limit",
    "rate_limit",
    "429",
    "tokens per min",
    "tpm",
)

_CREDIT_EXHAUSTED_CODES: frozenset[str] = frozenset(
    {
        "insufficient_quota",
        "billing_hard_limit_reached",
        "credit_balance_exhausted",
        "opensre_credits_exhausted",
    }
)

_CREDIT_EXHAUSTED_HINTS: tuple[str, ...] = (
    "insufficient_quota",
    "billing_hard_limit_reached",
    "credit_balance_exhausted",
    "exceeded your current quota",
    "credit balance is too low",
    "credit balance too low",
    "no credits remaining",
)


def _structured_error_code(exc: BaseException) -> str | None:
    """Return OpenAI-style ``exc.code`` or ``body.error.code``, else None."""
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        return code
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error_obj = body.get("error")
        if isinstance(error_obj, dict):
            nested_code = error_obj.get("code")
            if isinstance(nested_code, str):
                return nested_code
    return None


class LLMCreditExhaustedError(RuntimeError):
    """Fatal provider billing/quota exhaustion; retries will not help."""


class OpenSRECreditsExhaustedError(LLMCreditExhaustedError):
    """OpenSRE hosted credits are exhausted; Stripe billing can restore service."""

    def __init__(self, message: str, *, upgrade_url: str | None = None) -> None:
        super().__init__(message)
        self.upgrade_url = upgrade_url


# Stable substring every credit-exhausted message carries. Surfaces match on it
# to attach their own recovery hint (e.g. the shell's ``/model`` command).
CREDIT_EXHAUSTED_MARKER = "credit exhausted (provider billing/quota)"


def credit_exhaustion_reason(exc: BaseException) -> LLMCreditErrorReason | None:
    """Identify hosted or provider credit exhaustion from exception types and codes."""
    code = _structured_error_code(exc)
    if isinstance(exc, OpenSRECreditsExhaustedError) or code == "opensre_credits_exhausted":
        return LLMCreditErrorReason.OPENSRE
    if isinstance(exc, LLMCreditExhaustedError) or code in _CREDIT_EXHAUSTED_CODES:
        return LLMCreditErrorReason.PROVIDER
    return None


def is_rate_limit_error(exc: BaseException) -> bool:
    """Return True for transient TPM/429 errors, excluding billing exhaustion."""
    if is_credit_exhausted_error(exc):
        return False
    text = str(exc).lower()
    return any(hint in text for hint in _RATE_LIMIT_HINTS)


def is_credit_exhausted_error(exc: BaseException) -> bool:
    """Return True when the provider reports out-of-credit or quota exhaustion."""
    structured_code = _structured_error_code(exc)
    if structured_code is not None and structured_code in _CREDIT_EXHAUSTED_CODES:
        return True
    text = str(exc).lower()
    return any(hint in text for hint in _CREDIT_EXHAUSTED_HINTS)


def _structured_retry_delay_seconds(exc: BaseException) -> float | None:
    """Return Gemini ``RetryInfo.retryDelay`` in seconds, or None."""
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return None
    error_obj = body.get("error")
    if not isinstance(error_obj, dict):
        return None
    for detail in error_obj.get("details") or []:
        delay = detail.get("retryDelay") if isinstance(detail, dict) else None
        if delay:
            match = re.match(r"^(\d+(?:\.\d+)?)\s*s$", str(delay).strip())
            if match:
                return float(match.group(1))
    return None


def extract_retry_after_seconds(exc: BaseException) -> float | None:
    """Return provider-suggested retry delay in seconds, capped at RETRY_AFTER_MAX_SEC."""
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)
        if headers is not None:
            retry_after = headers.get("retry-after") if hasattr(headers, "get") else None
            if retry_after is not None:
                try:
                    seconds = float(retry_after)
                    if seconds >= 0:
                        return min(seconds, RETRY_AFTER_MAX_SEC)
                except (ValueError, TypeError):
                    # retry-after header was not a numeric delay; try other sources
                    pass

    structured = _structured_retry_delay_seconds(exc)
    if structured is not None:
        return min(structured, RETRY_AFTER_MAX_SEC)

    match = _BODY_RETRY_HINT_RE.search(str(exc))
    if match:
        value = float(match.group(1))
        if match.group(2).lower() == "ms":
            value /= 1000
        return min(value, RETRY_AFTER_MAX_SEC)

    return None


def maybe_raise_credit_exhausted(provider_name: str, err: BaseException) -> None:
    """Raise LLMCreditExhaustedError when billing/quota exhaustion is detected."""
    if not is_credit_exhausted_error(err):
        return

    if _structured_error_code(err) == "opensre_credits_exhausted":
        # The proxy is the authority: a cached funded balance is now wrong.
        from config.account_credits import reset_hosted_credits_cache

        reset_hosted_credits_cache()
        body = getattr(err, "body", None)
        error_obj = body.get("error") if isinstance(body, dict) else None
        details = error_obj if isinstance(error_obj, dict) else body
        raw_upgrade_url = details.get("upgrade_url") if isinstance(details, dict) else None
        upgrade_url = (
            raw_upgrade_url
            if isinstance(raw_upgrade_url, str) and re.fullmatch(r"https?://\S+", raw_upgrade_url)
            else None
        )
        destination = f" Upgrade or top up at {upgrade_url}." if upgrade_url else ""
        raise OpenSRECreditsExhaustedError(
            f"OpenSRE {CREDIT_EXHAUSTED_MARKER}. Your hosted credits are exhausted.{destination}",
            upgrade_url=upgrade_url,
        ) from err

    raise LLMCreditExhaustedError(
        f"{provider_name} {CREDIT_EXHAUSTED_MARKER}. "
        f"To keep going, switch to another configured LLM provider — or top up "
        f"your balance / raise the spending cap at the {provider_name} console. "
        f"Original error: {err}"
    ) from err


def rate_limit_sleep_seconds(err: BaseException, fallback_backoff: float) -> float:
    """Pick a jittered sleep duration for a rate-limit retry."""
    suggested = extract_retry_after_seconds(err)
    if suggested is not None:
        sleep_sec = suggested * random.uniform(0.9, 1.1)  # noqa: S311
        logger.warning(
            "[llm] rate-limited, honoring Retry-After=%.2fs (sleeping %.2fs after jitter)",
            suggested,
            sleep_sec,
        )
        return sleep_sec
    sleep_sec = random.uniform(0.0, fallback_backoff)  # noqa: S311
    logger.warning(
        "[llm] rate-limited, no Retry-After hint; sleeping %.2fs (jitter from [0, %.1fs])",
        sleep_sec,
        fallback_backoff,
    )
    return sleep_sec


def retry_on_rate_limit[T](
    fn: Callable[[], T],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_backoff_sec: float = DEFAULT_INITIAL_BACKOFF_SEC,
    label: str = "llm",
) -> T:
    """Call ``fn``, retrying rate-limit errors with full-jitter exponential backoff."""
    backoff = initial_backoff_sec
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as exc:
            if not is_rate_limit_error(exc):
                raise
            if attempt == max_attempts - 1:
                logger.warning(
                    "[%s] rate-limited after %d attempts, giving up: %s",
                    label,
                    max_attempts,
                    exc,
                )
                raise
            sleep_sec = random.uniform(0.0, backoff)  # noqa: S311
            logger.warning(
                "[%s] rate-limited, retrying in %.2fs (jitter from [0, %.1f]s) (attempt %d/%d)",
                label,
                sleep_sec,
                backoff,
                attempt + 1,
                max_attempts,
            )
            time.sleep(sleep_sec)
            backoff *= 2
    raise RuntimeError("retry_on_rate_limit exhausted without raise")  # pragma: no cover

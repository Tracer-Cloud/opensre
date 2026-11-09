"""Tests for the shared LLM rate-limit retry helper."""

from __future__ import annotations

import pytest

# Single import style for the helper module. We use the module alias for
# every symbol (``llm_retry.is_rate_limit_error``, ``llm_retry.retry_on_rate_limit``,
# etc.) so monkeypatching ``llm_retry.time`` and ``llm_retry.random`` shares
# the same lookup path as the symbols under test. Satisfies CodeQL
# "Module imported with both 'import' and 'import from'" (a from-import
# alongside a module-import flags as mixed-style).
import core.llm.shared.llm_retry as llm_retry

# --------------------------------------------------------------------------- #
# is_rate_limit_error — provider recognizer                                   #
# --------------------------------------------------------------------------- #


def test_is_rate_limit_error_recognizes_openai_wrapper() -> None:
    assert llm_retry.is_rate_limit_error(RuntimeError("OpenAI rate limit exceeded: 429"))
    assert llm_retry.is_rate_limit_error(
        RuntimeError(
            "Rate limit reached for gpt-4o-2024-11-20 ... "
            "tokens per min (TPM): Limit 30000, Used 29248."
        )
    )


def test_is_rate_limit_error_recognizes_anthropic_wrapper() -> None:
    assert llm_retry.is_rate_limit_error(RuntimeError("Anthropic rate limit exceeded: 429"))


def test_is_rate_limit_error_recognizes_structured_code() -> None:
    assert llm_retry.is_rate_limit_error(RuntimeError("provider error: rate_limit_exceeded"))


def test_is_rate_limit_error_case_insensitive() -> None:
    assert llm_retry.is_rate_limit_error(RuntimeError("RATE LIMIT EXCEEDED"))
    assert llm_retry.is_rate_limit_error(RuntimeError("TPM Exceeded"))


def test_is_rate_limit_error_does_not_match_unrelated_errors() -> None:
    # Don't retry 400 schema errors, generic exceptions, or model-not-found.
    assert not llm_retry.is_rate_limit_error(RuntimeError("Anthropic request rejected (HTTP 400)"))
    assert not llm_retry.is_rate_limit_error(ValueError("invalid input schema"))
    assert not llm_retry.is_rate_limit_error(RuntimeError("OpenAI model 'foo' not found"))


def test_is_rate_limit_error_does_not_match_credit_exhaustion() -> None:
    """Credit exhaustion can surface as 429 with 'rate limit' in surrounding
    text, but it's NOT transient. is_rate_limit_error must filter it out so
    the retry path never fires on a dead account."""
    err = RuntimeError(
        "OpenAI rate limit exceeded: Error code: 429 - "
        "{'error': {'message': 'You exceeded your current quota', "
        "'code': 'insufficient_quota'}}"
    )
    # Although "rate limit" is in the text, the credit hint wins → False.
    assert not llm_retry.is_rate_limit_error(err)
    assert llm_retry.is_credit_exhausted_error(err)


# --------------------------------------------------------------------------- #
# is_credit_exhausted_error — provider billing/quota recognizer               #
# --------------------------------------------------------------------------- #


def test_is_credit_exhausted_recognizes_openai_insufficient_quota() -> None:
    assert llm_retry.is_credit_exhausted_error(
        RuntimeError(
            "Error code: 429 - {'error': {'message': "
            "'You exceeded your current quota, please check your plan and billing details.', "
            "'code': 'insufficient_quota'}}"
        )
    )


def test_is_credit_exhausted_recognizes_openai_billing_hard_limit() -> None:
    assert llm_retry.is_credit_exhausted_error(
        RuntimeError("billing_hard_limit_reached for organization org-xyz")
    )


def test_is_credit_exhausted_recognizes_anthropic_credit_balance() -> None:
    assert llm_retry.is_credit_exhausted_error(
        RuntimeError(
            "Anthropic request rejected (HTTP 400): "
            "{'error': {'message': 'Your credit balance is too low to access the Anthropic API.'}}"
        )
    )


def test_is_credit_exhausted_does_not_match_transient_rate_limit() -> None:
    # Pure TPM rate limit — no credit/quota hint. Must NOT match.
    err = RuntimeError(
        "Rate limit reached for gpt-4o-2024-11-20 ... "
        "tokens per min (TPM): Limit 30000, Used 29248. Please try again in 94ms."
    )
    assert not llm_retry.is_credit_exhausted_error(err)
    assert llm_retry.is_rate_limit_error(err)


def test_is_credit_exhausted_does_not_match_unrelated_errors() -> None:
    assert not llm_retry.is_credit_exhausted_error(ValueError("invalid schema"))
    assert not llm_retry.is_credit_exhausted_error(RuntimeError("model 'foo' not found"))


# --------------------------------------------------------------------------- #
# Structured-code detection — OpenAI's stable error-code enum, reword-proof   #
# (documented at platform.openai.com/docs/guides/error-codes).                #
# --------------------------------------------------------------------------- #


class _FakeOpenAIAPIError(Exception):
    """Shape-compatible with openai.APIError for the bits we read."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        body: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.body = body


def test_is_credit_exhausted_uses_structured_code_when_message_is_clean() -> None:
    """If OpenAI ever softens the body wording (e.g. drops "exceeded your
    current quota"), the structured ``code`` field still tells us. This is
    the whole point of preferring codes over text."""
    err = _FakeOpenAIAPIError(
        "We had trouble processing your request.",  # benign wording
        code="insufficient_quota",
    )
    assert llm_retry.is_credit_exhausted_error(err)


def test_is_credit_exhausted_uses_credit_balance_exhausted_code() -> None:
    """OpenAI also emits ``credit_balance_exhausted`` (empty prepaid balance)."""
    err = _FakeOpenAIAPIError(
        "We had trouble processing your request.",
        code="credit_balance_exhausted",
    )
    assert llm_retry.is_credit_exhausted_error(err)


def test_is_credit_exhausted_uses_body_error_code_when_top_level_is_none() -> None:
    """SDK sometimes leaves ``.code`` None and only fills the parsed
    response body. Our extractor falls through to ``body.error.code``."""
    err = _FakeOpenAIAPIError(
        "rate limit reached (benign wording)",
        code=None,
        body={"error": {"code": "billing_hard_limit_reached", "type": "billing"}},
    )
    assert llm_retry.is_credit_exhausted_error(err)


def test_is_credit_exhausted_returns_false_for_transient_rate_limit_code() -> None:
    """OpenAI's transient TPM throttle has ``code='rate_limit_exceeded'``.
    Must NOT classify it as fatal."""
    err = _FakeOpenAIAPIError(
        "Rate limit reached for gpt-4o ... tokens per min (TPM)",
        code="rate_limit_exceeded",
    )
    assert not llm_retry.is_credit_exhausted_error(err)
    assert llm_retry.is_rate_limit_error(err)


def test_is_credit_exhausted_ignores_non_string_code_attributes() -> None:
    """Defensive: an exception with ``.code`` set to a non-string
    (e.g. an int or None) should not crash the extractor."""
    err = _FakeOpenAIAPIError("rate limited", code=None)
    # Falls through to text matching; "rate limited" doesn't match any
    # credit hint, so returns False — not a crash.
    assert llm_retry.is_credit_exhausted_error(err) is False


def test_is_credit_exhausted_falls_back_to_text_for_anthropic_400() -> None:
    """Anthropic does NOT expose a billing-specific error code — credit-
    too-low surfaces as a generic invalid_request_error with the wording
    in the message body. Text matching is the only signal Anthropic gives
    us; this is documented at docs.anthropic.com/en/api/errors."""
    # Mimics anthropic.BadRequestError — no ``.code`` attr at all.
    err = RuntimeError(
        "Anthropic request rejected (HTTP 400): "
        "{'type': 'invalid_request_error', "
        "'message': 'Your credit balance is too low to access the Anthropic API.'}"
    )
    assert llm_retry.is_credit_exhausted_error(err)


def test_maybe_raise_credit_exhausted_offers_provider_switch_recovery() -> None:
    """The message leads with the in-tool recovery (switch providers), not only
    a pointer to the billing console."""
    with pytest.raises(llm_retry.LLMCreditExhaustedError) as excinfo:
        llm_retry.maybe_raise_credit_exhausted(
            "Anthropic", RuntimeError("Your credit balance is too low")
        )
    message = str(excinfo.value)
    assert "switch to another configured LLM provider" in message
    assert "Anthropic console" in message


def test_opensre_credit_exhaustion_preserves_stripe_upgrade_url() -> None:
    upgrade_url = "https://app.opensre.dev/usage"
    err = _FakeOpenAIAPIError(
        "Payment required",
        code="opensre_credits_exhausted",
        body={
            "message": "OpenSRE credits are exhausted",
            "code": "opensre_credits_exhausted",
            "upgrade_url": upgrade_url,
        },
    )

    with pytest.raises(llm_retry.OpenSRECreditsExhaustedError) as excinfo:
        llm_retry.maybe_raise_credit_exhausted("OpenAI", err)

    assert excinfo.value.upgrade_url == upgrade_url
    assert upgrade_url in str(excinfo.value)


def test_opensre_credit_exhaustion_rejects_unsafe_upgrade_url() -> None:
    err = _FakeOpenAIAPIError(
        "Payment required",
        code="opensre_credits_exhausted",
        body={
            "code": "opensre_credits_exhausted",
            "upgrade_url": "javascript:alert(1)",
        },
    )

    with pytest.raises(llm_retry.OpenSRECreditsExhaustedError) as excinfo:
        llm_retry.maybe_raise_credit_exhausted("OpenAI", err)

    assert excinfo.value.upgrade_url is None
    assert "javascript:" not in str(excinfo.value)


# --------------------------------------------------------------------------- #
# retry_on_rate_limit — control flow                                          #
# --------------------------------------------------------------------------- #


def test_retry_on_rate_limit_returns_immediately_on_success() -> None:
    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        return "ok"

    assert llm_retry.retry_on_rate_limit(fn, label="test") == "ok"
    assert calls["n"] == 1


def test_retry_on_rate_limit_recovers_after_transient_failures(monkeypatch) -> None:
    """Two 429s, then success — final result returned, 3 total calls."""
    monkeypatch.setattr(llm_retry.time, "sleep", lambda _s: None)

    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("OpenAI rate limit exceeded")
        return "ok"

    assert llm_retry.retry_on_rate_limit(fn, label="test") == "ok"
    assert calls["n"] == 3


def test_retry_on_rate_limit_reraises_after_exhausting(monkeypatch) -> None:
    """Always 429 — should re-raise after max_attempts. No silent None."""
    monkeypatch.setattr(llm_retry.time, "sleep", lambda _s: None)

    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        raise RuntimeError("Anthropic rate limit exceeded")

    with pytest.raises(RuntimeError, match="rate limit"):
        llm_retry.retry_on_rate_limit(fn, label="test")
    assert calls["n"] == llm_retry.DEFAULT_MAX_ATTEMPTS


def test_retry_on_rate_limit_does_not_retry_non_rate_limit_errors(monkeypatch) -> None:
    """A schema error fails fast — retrying a deterministic bug is wasted."""
    monkeypatch.setattr(llm_retry.time, "sleep", lambda _s: None)

    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        raise ValueError("invalid schema")

    with pytest.raises(ValueError, match="invalid schema"):
        llm_retry.retry_on_rate_limit(fn, label="test")
    # Exactly one attempt — no retry on non-rate-limit errors.
    assert calls["n"] == 1


def test_retry_on_rate_limit_respects_custom_max_attempts(monkeypatch) -> None:
    """Caller can lower / raise the retry count for their tolerance budget."""
    monkeypatch.setattr(llm_retry.time, "sleep", lambda _s: None)

    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        raise RuntimeError("rate limit exceeded")

    with pytest.raises(RuntimeError):
        llm_retry.retry_on_rate_limit(fn, label="test", max_attempts=5)
    assert calls["n"] == 5


def test_retry_on_rate_limit_applies_full_jitter_with_doubling_upper_bound(
    monkeypatch,
) -> None:
    """random.uniform must be called with (0, current_backoff), and the
    upper bound must double on each retry — that's the exponential growth
    inside the jitter envelope."""
    sleeps: list[float] = []
    jitter_bounds: list[tuple[float, float]] = []

    monkeypatch.setattr(llm_retry.time, "sleep", lambda s: sleeps.append(s))

    def fake_uniform(low: float, high: float) -> float:
        jitter_bounds.append((low, high))
        # Deterministic: pick the midpoint so the test asserts the sleep
        # value without needing to seed random.
        return (low + high) / 2

    monkeypatch.setattr(llm_retry.random, "uniform", fake_uniform)

    def fn() -> str:
        raise RuntimeError("rate limit exceeded")

    with pytest.raises(RuntimeError):
        llm_retry.retry_on_rate_limit(fn, label="test", initial_backoff_sec=2.0)

    # 3 attempts → 2 sleeps between them (no sleep after the final raise).
    # Full jitter envelope is [0, backoff], backoff doubles each retry.
    assert jitter_bounds == [(0.0, 2.0), (0.0, 4.0)]
    assert sleeps == [1.0, 2.0]  # midpoints from fake_uniform


# --------------------------------------------------------------------------- #
# extract_retry_after_seconds — provider hint parsing                         #
# --------------------------------------------------------------------------- #


class _FakeResponse:
    """Stand-in for httpx.Response with a ``.headers`` dict-like attribute."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def _err_with_response(headers: dict[str, str], msg: str = "rate limited") -> RuntimeError:
    err = RuntimeError(msg)
    err.response = _FakeResponse(headers)  # type: ignore[attr-defined]
    return err


def test_extract_retry_after_reads_integer_seconds_header() -> None:
    err = _err_with_response({"retry-after": "5"})
    assert llm_retry.extract_retry_after_seconds(err) == 5.0


def test_extract_retry_after_reads_fractional_seconds_header() -> None:
    err = _err_with_response({"retry-after": "0.094"})
    assert llm_retry.extract_retry_after_seconds(err) == pytest.approx(0.094)


def test_extract_retry_after_falls_back_to_body_text_milliseconds() -> None:
    # Real OpenAI 429 body text.
    err = RuntimeError(
        "OpenAI rate limit exceeded: Error code: 429 - "
        "Limit 30000, Used 29248, Requested 799. Please try again in 94ms."
    )
    assert llm_retry.extract_retry_after_seconds(err) == pytest.approx(0.094)


def test_extract_retry_after_falls_back_to_body_text_seconds() -> None:
    # 12s — below the llm_retry.RETRY_AFTER_MAX_SEC cap so we read the exact value.
    err = RuntimeError("rate limited: Please try again in 12s.")
    assert llm_retry.extract_retry_after_seconds(err) == 12.0


def test_extract_retry_after_caps_at_max() -> None:
    """A misconfigured provider returning ``retry-after: 3600`` must not
    hang the agent loop for an hour. Cap at llm_retry.RETRY_AFTER_MAX_SEC."""
    err = _err_with_response({"retry-after": "3600"})
    assert llm_retry.extract_retry_after_seconds(err) == llm_retry.RETRY_AFTER_MAX_SEC


def test_extract_retry_after_caps_body_hint_at_max() -> None:
    err = RuntimeError("rate limited: Please try again in 7200s.")
    assert llm_retry.extract_retry_after_seconds(err) == llm_retry.RETRY_AFTER_MAX_SEC


def test_extract_retry_after_returns_none_when_no_hint_present() -> None:
    # No response attached, no body hint — caller should fall back to jitter.
    assert llm_retry.extract_retry_after_seconds(RuntimeError("rate limited, generic")) is None
    # Response present but no retry-after header.
    err = _err_with_response({"x-other": "value"})
    assert llm_retry.extract_retry_after_seconds(err) is None


def _err_with_retry_delay(retry_delay: str, msg: str = "rate limited") -> RuntimeError:
    """Build an error carrying a Gemini-style RetryInfo body (no header)."""
    err = RuntimeError(msg)
    err.body = {  # type: ignore[attr-defined]
        "error": {
            "details": [
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay},
            ]
        }
    }
    return err


def test_extract_retry_after_reads_gemini_structured_retry_delay() -> None:
    # Gemini ships no retry-after header; the hint lives in error.details[].retryDelay.
    assert llm_retry.extract_retry_after_seconds(_err_with_retry_delay("5s")) == 5.0


def test_extract_retry_after_reads_gemini_fractional_retry_delay() -> None:
    err = _err_with_retry_delay("5.478s")
    assert llm_retry.extract_retry_after_seconds(err) == pytest.approx(5.478)


def test_extract_retry_after_caps_gemini_retry_delay_at_max() -> None:
    err = _err_with_retry_delay("120s")
    assert llm_retry.extract_retry_after_seconds(err) == llm_retry.RETRY_AFTER_MAX_SEC


def test_extract_retry_after_reads_gemini_retry_in_message() -> None:
    # Gemini's human-readable form: "Please retry in 8.5s".
    err = RuntimeError("Please retry in 8.5s due to quota exceeded")
    assert llm_retry.extract_retry_after_seconds(err) == 8.5


def test_extract_retry_after_tolerates_null_details() -> None:
    # A body with "details": null must not raise (no hint -> None).
    err = RuntimeError("rate limited")
    err.body = {"error": {"details": None}}  # type: ignore[attr-defined]
    assert llm_retry.extract_retry_after_seconds(err) is None


def test_extract_retry_after_skips_http_date_format() -> None:
    """HTTP-date Retry-After is allowed by RFC 7231 but rare. We don't parse
    it — fall through to body text or None."""
    err = _err_with_response({"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"})
    assert llm_retry.extract_retry_after_seconds(err) is None


def test_extract_retry_after_ignores_negative_header() -> None:
    """Negative retry-after is invalid per spec; treat as no hint and fall
    back. (Some misconfigured proxies have been observed sending -1.)"""
    err = _err_with_response({"retry-after": "-1"})
    assert llm_retry.extract_retry_after_seconds(err) is None


def test_retry_on_rate_limit_jitter_is_non_deterministic_under_real_random() -> None:
    """Without monkeypatching random, repeated identical calls must NOT produce
    identical sleep durations — confirms random.uniform is actually wired
    into the production path (not e.g. accidentally short-circuited to a
    constant by a future refactor)."""

    def fn() -> str:
        raise RuntimeError("rate limit exceeded")

    def capture_one_run() -> list[float]:
        sleeps: list[float] = []
        original_sleep = llm_retry.time.sleep
        llm_retry.time.sleep = sleeps.append  # type: ignore[assignment]
        try:
            with pytest.raises(RuntimeError):
                llm_retry.retry_on_rate_limit(fn, label="test", initial_backoff_sec=2.0)
        finally:
            llm_retry.time.sleep = original_sleep  # type: ignore[assignment]
        return sleeps

    sleeps_across_runs = [capture_one_run() for _ in range(3)]

    # At least one pair of runs must differ — vanishingly unlikely for three
    # independent uniform draws to coincide on all values. If this is ever
    # flaky on real hardware, jitter is genuinely broken.
    assert any(
        sleeps_across_runs[i] != sleeps_across_runs[j]
        for i in range(len(sleeps_across_runs))
        for j in range(i + 1, len(sleeps_across_runs))
    ), "sleep durations identical across runs — jitter is not being applied"

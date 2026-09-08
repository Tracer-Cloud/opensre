"""Tests for interactive-shell session token accounting."""

from __future__ import annotations

import time

from core.agent_harness.accounting.token_accounting import (
    build_llm_run_info,
    estimate_tokens,
    format_token_total,
    record_llm_turn,
)
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.streaming import _CHARS_PER_TOKEN


def test_estimate_tokens_uses_chars_per_token_ratio() -> None:
    text = "x" * (8 * _CHARS_PER_TOKEN)
    assert estimate_tokens(text) == 8


def test_record_token_usage_accumulates_on_session() -> None:
    session = Session()
    record_llm_turn(session, prompt="a" * 40, response="b" * 20)
    assert session.tokens.totals == {
        "input": 10,
        "output": 5,
        "input_estimated": 10,
        "output_estimated": 5,
    }
    assert session.tokens.call_count == 1
    assert session.tokens.has_estimates is True
    record_llm_turn(session, prompt="c" * 8, response="d" * 4)
    assert session.tokens.totals == {
        "input": 12,
        "output": 6,
        "input_estimated": 12,
        "output_estimated": 6,
    }
    assert session.tokens.call_count == 2


def test_record_llm_turn_uses_provider_counts_when_present() -> None:
    session = Session()
    inp, out, estimated = record_llm_turn(
        session,
        prompt="ignored when counts supplied",
        response="also ignored",
        input_tokens=1200,
        output_tokens=80,
    )
    assert (inp, out, estimated) == (1200, 80, False)
    assert session.tokens.totals == {
        "input": 1200,
        "output": 80,
        "input_measured": 1200,
        "output_measured": 80,
    }
    assert session.tokens.has_estimates is False


def test_format_token_total_shows_mixed_breakdown() -> None:
    session = Session()
    record_llm_turn(
        session,
        prompt="x",
        response="y",
        input_tokens=300,
        output_tokens=40,
    )
    record_llm_turn(session, prompt="a" * 400, response="b" * 80)
    assert format_token_total(session, direction="input") == (
        "input tokens",
        "400 (300 provider + 100 est.)",
    )
    assert format_token_total(session, direction="output") == (
        "output tokens",
        "60 (40 provider + 20 est.)",
    )


def test_build_llm_run_info_records_tokens_and_metadata() -> None:
    session = Session()

    class _Client:
        _model = "claude-test"
        _provider_label = "Anthropic"

    run = build_llm_run_info(
        session=session,
        prompt="a" * 40,
        response_text="b" * 20,
        client=_Client(),
        started=time.monotonic() - 0.05,
    )
    assert run.model == "claude-test"
    assert run.provider == "anthropic"
    assert run.input_tokens == 10
    assert run.output_tokens == 5
    assert run.response_text == "b" * 20
    assert run.latency_ms is not None and run.latency_ms >= 0


def test_coerce_usage_tokens_accepts_float_counts() -> None:
    from core.llm.shared.usage import coerce_usage_tokens

    assert coerce_usage_tokens(
        {"input_tokens": 512.0, "output_tokens": 64.0},
        input_key="input_tokens",
        output_key="output_tokens",
    ) == (512, 64)


def test_record_token_usage_skips_zero_counts() -> None:
    session = Session()
    session.tokens.record()
    assert session.tokens.totals == {}
    assert session.tokens.call_count == 0


def test_provider_usage_tap_records_each_model_call_and_skips_unreported_ones() -> None:
    # Arrange: a session and the runtime-event tap wrapping an inner observer.
    from core.agent_harness.accounting.token_accounting import tap_provider_usage
    from core.agent_harness.session.session_core import SessionCore
    from core.events import ProviderRequestEndEvent

    session = SessionCore()
    seen: list[object] = []
    callback = tap_provider_usage(seen.append, session)

    # Act: two model calls with usage, one without, and an unrelated event.
    callback(
        ProviderRequestEndEvent(
            iteration=1, has_tool_calls=True, data={"input_tokens": 120, "output_tokens": 30}
        )
    )
    callback(ProviderRequestEndEvent(iteration=2, has_tool_calls=False, data={}))
    callback(
        ProviderRequestEndEvent(
            iteration=3, has_tool_calls=False, data={"input_tokens": 200, "output_tokens": 50}
        )
    )

    # Assert: exact counts land per call, nothing estimated, every event still forwarded.
    assert session.tokens.io_totals() == (320, 80)
    assert session.tokens.call_count == 2
    assert session.tokens.has_estimates is False
    assert len(seen) == 3

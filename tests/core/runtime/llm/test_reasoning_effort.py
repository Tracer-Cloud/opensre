"""Unit tests for session-scoped reasoning effort helpers."""

from __future__ import annotations

import os
import threading

import pytest

from config.llm_reasoning_effort import (
    ReasoningEffort,
    ReasoningEffortChoice,
    apply_reasoning_effort,
    describe_reasoning_effort_default,
    display_reasoning_effort,
    get_active_reasoning_effort,
    infer_reasoning_effort_default,
    model_default_reasoning_effort,
    parse_reasoning_effort,
    resolve_reasoning_effort,
    runtime_reasoning_effort,
)

_ENV_KEY = "OPENSRE_REASONING_EFFORT"


@pytest.fixture(autouse=True)
def _clear_reasoning_effort_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENV_KEY, raising=False)


def test_parse_reasoning_effort_accepts_enum_values() -> None:
    assert parse_reasoning_effort("low") is ReasoningEffort.LOW
    assert parse_reasoning_effort(" MAX ") is ReasoningEffort.MAX
    assert parse_reasoning_effort("invalid") is None


def test_runtime_reasoning_effort_maps_max_to_xhigh() -> None:
    assert runtime_reasoning_effort(ReasoningEffort.MAX) == "xhigh"
    assert runtime_reasoning_effort(ReasoningEffort.HIGH) == "high"


def test_reasoning_effort_members_round_trip_from_string() -> None:
    assert set(ReasoningEffort) == {
        ReasoningEffort.LOW,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.HIGH,
        ReasoningEffort.XHIGH,
        ReasoningEffort.MAX,
    }
    assert ReasoningEffort("max") is ReasoningEffort.MAX
    assert ReasoningEffort.MAX == "max"


def test_apply_none_preserves_shell_env_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV_KEY, "high")

    with apply_reasoning_effort(None):
        assert get_active_reasoning_effort() == "high"
        assert _ENV_KEY in os.environ

    assert os.environ.get(_ENV_KEY) == "high"


def test_apply_non_none_overrides_env_until_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV_KEY, "low")

    with apply_reasoning_effort(ReasoningEffort.HIGH):
        assert get_active_reasoning_effort() == "high"

    assert get_active_reasoning_effort() == "low"


def test_concurrent_threads_do_not_cross_session_overrides() -> None:
    """Session overrides use contextvars (per-thread), not shared os.environ."""
    mid = threading.Barrier(2)
    observed: dict[int, str | None] = {}

    def worker(tid: int, effort: ReasoningEffortChoice) -> None:
        with apply_reasoning_effort(effort):
            mid.wait()
            observed[tid] = get_active_reasoning_effort()

    threads = (
        threading.Thread(target=worker, args=(1, ReasoningEffort.HIGH)),
        threading.Thread(target=worker, args=(2, ReasoningEffort.LOW)),
    )
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert observed[1] == "high"
    assert observed[2] == "low"


def test_display_reasoning_effort_formats_max_alias() -> None:
    assert display_reasoning_effort(ReasoningEffort.MAX) == "max (runtime: xhigh)"


def test_display_reasoning_effort_accepts_plain_string() -> None:
    assert display_reasoning_effort("max") == "max (runtime: xhigh)"


def test_display_reasoning_effort_formats_default_in_parentheses() -> None:
    assert display_reasoning_effort(None) == "(default)"


def test_infer_reasoning_effort_default_for_openai_gpt_5_2() -> None:
    assert infer_reasoning_effort_default("openai", "gpt-5.2") == "none"


def test_gpt_6_1_defaults_to_extra_high_until_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    assert model_default_reasoning_effort("gpt-6.1-sol") == "xhigh"
    assert model_default_reasoning_effort("openai/gpt-6.1-sol") == "xhigh"
    assert model_default_reasoning_effort("gpt-5.6-sol") is None
    assert resolve_reasoning_effort("gpt-6.1-sol") == "xhigh"
    assert resolve_reasoning_effort("gpt-5.4-mini") is None
    assert infer_reasoning_effort_default("openai", "gpt-6.1-sol") == "xhigh"
    assert infer_reasoning_effort_default("codex", "gpt-6.1-sol") == "xhigh"
    assert (
        describe_reasoning_effort_default("openai", "gpt-6.1-sol") == "openai · gpt-6.1-sol: xhigh"
    )

    monkeypatch.setenv(_ENV_KEY, "low")
    assert resolve_reasoning_effort("gpt-6.1-sol") == "low"


def test_describe_reasoning_effort_default_for_unsupported_provider() -> None:
    assert (
        describe_reasoning_effort_default("anthropic", "claude-opus-4-7")
        == "anthropic does not use reasoning-effort overrides"
    )

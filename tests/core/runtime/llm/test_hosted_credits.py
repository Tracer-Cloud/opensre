"""Hosted OpenSRE credits: admit empty ledgers, fail open on fetch errors."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from config.account_credits import AccountCredits, HostedCreditsKindValue, HostedCreditsRead
from core.llm.hosted_credits import admit_hosted_credits, hosted_credits_prompt_lines
from core.llm.shared.llm_retry import OpenSRECreditsExhaustedError


def _credits(total: int) -> AccountCredits:
    return AccountCredits(
        total=total,
        monthly=total,
        monthly_limit=100_000,
        top_up=0,
        resets_at=None,
        plan_id="team",
    )


def _read(
    *, total: int | None, kind: HostedCreditsKindValue = HostedCreditsKindValue.OK
) -> HostedCreditsRead:
    credits = None if total is None else _credits(total)
    return HostedCreditsRead(
        kind,
        credits,
        "detail",
        usage_url="https://app.opensre.com/usage",
    )


@pytest.fixture
def hosted_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.llm.hosted_credits.account_llm_route",
        lambda: SimpleNamespace(
            base_url="https://app.opensre.com/api/llm/v1", model="gpt-5.4-mini"
        ),
    )


def test_admit_skips_when_not_on_the_hosted_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.llm.hosted_credits.account_llm_route", lambda: None)

    def _should_not_read() -> HostedCreditsRead:
        raise AssertionError("ledger must not be read without a hosted route")

    monkeypatch.setattr("core.llm.hosted_credits.fetch_hosted_credits", _should_not_read)
    admit_hosted_credits()


def test_admit_blocks_a_verified_zero_balance(
    monkeypatch: pytest.MonkeyPatch, hosted_route: None
) -> None:
    monkeypatch.setattr(
        "core.llm.hosted_credits.fetch_hosted_credits",
        lambda: _read(total=0),
    )

    with pytest.raises(OpenSRECreditsExhaustedError) as excinfo:
        admit_hosted_credits()

    assert excinfo.value.upgrade_url == "https://app.opensre.com/usage"
    assert "hosted credits are exhausted" in str(excinfo.value)


def test_admit_allows_a_positive_balance(
    monkeypatch: pytest.MonkeyPatch, hosted_route: None
) -> None:
    monkeypatch.setattr(
        "core.llm.hosted_credits.fetch_hosted_credits",
        lambda: _read(total=12_500),
    )
    admit_hosted_credits()


def test_admit_fails_open_when_the_ledger_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, hosted_route: None
) -> None:
    monkeypatch.setattr(
        "core.llm.hosted_credits.fetch_hosted_credits",
        lambda: _read(total=None, kind=HostedCreditsKindValue.UNAVAILABLE),
    )
    admit_hosted_credits()


def test_prompt_lines_quote_remaining_credits_and_admission(
    monkeypatch: pytest.MonkeyPatch, hosted_route: None
) -> None:
    monkeypatch.setattr(
        "core.llm.hosted_credits.cached_hosted_credits",
        lambda: _read(total=12_500),
    )
    lines = hosted_credits_prompt_lines()
    joined = "\n".join(lines)
    assert "OpenSRE hosted credits remaining are 12,500" in joined
    assert "hosted LLM requests are allowed" in joined


def test_prompt_lines_forbid_spend_when_the_ledger_is_empty(
    monkeypatch: pytest.MonkeyPatch, hosted_route: None
) -> None:
    monkeypatch.setattr(
        "core.llm.hosted_credits.cached_hosted_credits",
        lambda: _read(total=0),
    )
    lines = hosted_credits_prompt_lines()
    joined = "\n".join(lines)
    assert "OpenSRE hosted credits remaining are 0" in joined
    assert "hosted LLM requests must not run" in joined


def test_prompt_lines_stay_empty_off_the_hosted_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.llm.hosted_credits.account_llm_route", lambda: None)
    assert hosted_credits_prompt_lines() == ()


def test_prompt_lines_do_not_fetch_when_the_cache_is_empty(
    monkeypatch: pytest.MonkeyPatch, hosted_route: None
) -> None:
    monkeypatch.setattr("core.llm.hosted_credits.cached_hosted_credits", lambda: None)

    def _should_not_read() -> HostedCreditsRead:
        raise AssertionError("prompt assembly must stay offline")

    monkeypatch.setattr("core.llm.hosted_credits.fetch_hosted_credits", _should_not_read)
    assert hosted_credits_prompt_lines() == ()

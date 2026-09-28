"""Hosted coding-agent credentials stay in the child env, never argv."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from integrations.coding_agent.hosted_credentials import hosted_openai_subprocess_env


def test_unsigned_session_has_no_hosted_openai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.account.account_llm_route", lambda: None)
    monkeypatch.setattr("config.account.resolve_account_token", lambda: "osre_pat_secret")

    assert hosted_openai_subprocess_env() is None


def test_signed_in_route_without_token_has_no_hosted_openai_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "config.account.account_llm_route",
        lambda: SimpleNamespace(
            base_url="https://app.opensre.com/api/llm/v1", model="gpt-5.4-mini"
        ),
    )
    monkeypatch.setattr("config.account.resolve_account_token", lambda: "")

    assert hosted_openai_subprocess_env() is None


def test_signed_in_session_puts_the_account_token_only_in_openai_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "config.account.account_llm_route",
        lambda: SimpleNamespace(
            base_url="https://app.opensre.com/api/llm/v1", model="gpt-5.4-mini"
        ),
    )
    monkeypatch.setattr("config.account.resolve_account_token", lambda: "osre_pat_secret")

    env = hosted_openai_subprocess_env()

    assert env == {
        "OPENAI_API_KEY": "osre_pat_secret",
        "OPENAI_BASE_URL": "https://app.opensre.com/api/llm/v1",
    }

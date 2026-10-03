"""Missing-key LLM failures name a real provider id, in the commands of the reader's surface."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pytest

from config.llm_auth.provider_catalog import PROVIDER_SPECS, ProviderSpec
from core.agent_harness.accounting.token_accounting import resolve_provider_id
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.session.terminal_access import execute_cli_onboard_on_missing_key
from surfaces.interactive_shell.session import Session
from surfaces.shared.llm_setup.auth_profiles import resolve_auth_profile

_MISSING_KEY = (
    "Missing credentials. Please pass an `api_key`, `workload_identity`, "
    "`admin_api_key`, or set the `OPENAI_API_KEY` or `OPENAI_ADMIN_KEY` "
    "environment variable."
)
_AUTH_LOGIN_ID = re.compile(r"`(?:/auth login|opensre auth login) ([^`]+)`")
_API_KEY_PROVIDERS = [spec for spec in PROVIDER_SPECS if spec.uses_open_sre_api_key]


@dataclass(frozen=True)
class _KeyedClient:
    """Any client that authenticates with one OpenSRE-managed API-key env."""

    _api_key_env: str


def _signed_out() -> None:
    return None


def test_missing_key_queues_onboard() -> None:
    session = Session()

    text = execute_cli_onboard_on_missing_key(session, _MISSING_KEY, provider="openrouter")

    assert text is not None
    assert "No API key is set for openrouter" in text
    assert session.terminal.pending_prompt_default == "/onboard"
    assert session.terminal.pending_prompt_autosubmit is True


def test_rejected_key_does_not_queue_onboard() -> None:
    session = Session()

    text = execute_cli_onboard_on_missing_key(
        session, "401 Unauthorized: the key was rejected.", provider="openrouter"
    )

    assert text is None
    assert session.terminal.pending_prompt_default is None
    assert session.terminal.pending_prompt_autosubmit is False


def test_exclusive_stdin_does_not_requeue_onboard() -> None:
    session = Session()
    session.terminal.exclusive_stdin_active = True

    text = execute_cli_onboard_on_missing_key(session, _MISSING_KEY, provider="openrouter")

    assert text is not None
    assert session.terminal.pending_prompt_default is None
    assert session.terminal.pending_prompt_autosubmit is False


@pytest.mark.parametrize("spec", _API_KEY_PROVIDERS, ids=lambda spec: spec.value)
@pytest.mark.parametrize("in_shell", [True, False], ids=["shell", "cli"])
def test_missing_key_guidance_names_an_id_auth_login_accepts(
    spec: ProviderSpec, in_shell: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: `custom_openai` came from the client's display label, not the catalog."""
    monkeypatch.setattr("config.account.account_llm_route", _signed_out)
    session = Session() if in_shell else SessionCore(store=InMemorySessionStore())
    provider = resolve_provider_id(_KeyedClient(spec.api_key_env))

    text = execute_cli_onboard_on_missing_key(session, _MISSING_KEY, provider=provider)

    assert text is not None
    match = _AUTH_LOGIN_ID.search(text)
    assert match is not None, text
    assert match.group(0).startswith("`/auth login" if in_shell else "`opensre auth login")
    # The canonical id, not merely one the underscore alias happens to rescue.
    assert match.group(1) == spec.value
    assert resolve_auth_profile(match.group(1)).provider_value == spec.value


def test_headless_session_gets_cli_commands_and_nothing_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `opensre ask` and gateway turns have no prompt to run a queued slash command.
    monkeypatch.setattr("config.account.account_llm_route", _signed_out)
    session = SessionCore(store=InMemorySessionStore())

    text = execute_cli_onboard_on_missing_key(session, _MISSING_KEY, provider="custom-openai")

    assert text is not None
    assert "`opensre onboard`" in text
    assert "`opensre account login`" in text
    assert "`/" not in text
    assert not getattr(session, "_headless_turn_outcome_hints", None)

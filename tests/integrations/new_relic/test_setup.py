"""Tests for the New Relic setup spec.

Pins two things per plan.md §4: the spec is wired through the shared setup
flow (service/verify/field shape), and submitted, non-default credentials
round-trip through the tiers FR-5 assigns them by env-var name — ``api_key``
(terminal ``key``) to the keyring, ``account_id``/``base_url`` (terminal
``id``/``url``) to plain ``.env``.
"""

from __future__ import annotations

import dataclasses

import pytest

import integrations.setup_flow as setup_flow
from config.constants.new_relic import (
    NEW_RELIC_ACCOUNT_ID_ENV,
    NEW_RELIC_API_KEY_ENV,
    NEW_RELIC_BASE_URL_ENV,
)
from integrations.new_relic.config import DEFAULT_NEW_RELIC_BASE_URL
from integrations.new_relic.setup import (
    ACCOUNT_ID_FIELD,
    API_KEY_FIELD,
    BASE_URL_FIELD,
    NEW_RELIC_SETUP,
)
from integrations.new_relic.verifier import verify_new_relic

# Obviously-fake, non-default values — a default would "round-trip" even
# through a spec that wrote nothing at all.
_SUBMITTED = {
    API_KEY_FIELD: "NRAK-test-fake-not-a-real-key-00",
    ACCOUNT_ID_FIELD: "7654321",
    BASE_URL_FIELD: "https://api.eu.newrelic.com",
}


def test_new_relic_setup_spec_is_wired_through_the_shared_flow() -> None:
    assert NEW_RELIC_SETUP.service == "new_relic"
    assert NEW_RELIC_SETUP.verify is verify_new_relic
    fields = {field.name: field for field in NEW_RELIC_SETUP.fields}

    assert fields[API_KEY_FIELD].secret is True
    assert fields[API_KEY_FIELD].env_var == NEW_RELIC_API_KEY_ENV
    assert fields[ACCOUNT_ID_FIELD].secret is False
    assert fields[ACCOUNT_ID_FIELD].env_var == NEW_RELIC_ACCOUNT_ID_ENV
    assert fields[BASE_URL_FIELD].env_var == NEW_RELIC_BASE_URL_ENV
    assert fields[BASE_URL_FIELD].default == DEFAULT_NEW_RELIC_BASE_URL


def test_apply_setup_round_trips_submitted_credentials_to_the_right_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets: dict[str, str] = {}
    env_values: dict[str, str] = {}

    def _fake_sync_env_values(values: dict[str, str]) -> object:
        env_values.update(values)
        return object()

    monkeypatch.setattr(setup_flow, "sync_env_secret", secrets.__setitem__)
    monkeypatch.setattr(setup_flow, "sync_env_values", _fake_sync_env_values)
    monkeypatch.setattr(setup_flow, "upsert_integration", lambda _service, _payload: None)
    monkeypatch.setattr(setup_flow, "push_webapp_org_integration", lambda _service, _creds: None)

    # Verification is New Relic's own concern (pinned in test_client.py); skip
    # it here so this test stays on one question: do the values land where
    # FR-5 says they should, under the names the spec declares?
    outcome = setup_flow.apply_setup(dataclasses.replace(NEW_RELIC_SETUP, verify=None), _SUBMITTED)

    assert outcome.ok, outcome.detail
    assert secrets == {NEW_RELIC_API_KEY_ENV: _SUBMITTED[API_KEY_FIELD]}
    assert env_values == {
        NEW_RELIC_ACCOUNT_ID_ENV: _SUBMITTED[ACCOUNT_ID_FIELD],
        NEW_RELIC_BASE_URL_ENV: _SUBMITTED[BASE_URL_FIELD],
    }


def test_apply_setup_requires_both_api_key_and_account_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_flow, "sync_env_secret", lambda *_args: None)
    monkeypatch.setattr(setup_flow, "sync_env_values", lambda _values: object())
    monkeypatch.setattr(setup_flow, "upsert_integration", lambda _service, _payload: None)
    monkeypatch.setattr(setup_flow, "push_webapp_org_integration", lambda _service, _creds: None)

    missing_account_id = dict(_SUBMITTED)
    missing_account_id[ACCOUNT_ID_FIELD] = ""

    outcome = setup_flow.apply_setup(
        dataclasses.replace(NEW_RELIC_SETUP, verify=None), missing_account_id
    )

    assert outcome.ok is False
    assert "account ID" in outcome.detail

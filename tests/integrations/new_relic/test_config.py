"""Tests for New Relic config normalization and host restriction.

Pins the security fix from PR review: ``NewRelicClient`` sends the ``API-Key``
header to whatever ``base_url`` points at, so an unrestricted host would
exfiltrate the credential during a probe or query. ``base_url`` must resolve
to one of New Relic's documented US/EU/JP hosts.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from integrations.new_relic.config import DEFAULT_NEW_RELIC_BASE_URL, NewRelicIntegrationConfig

_FAKE_API_KEY = "NRAK-test-fake-not-a-real-key-00"
_FAKE_ACCOUNT_ID = "1234567"


def test_config_defaults_to_the_us_host() -> None:
    config = NewRelicIntegrationConfig(api_key=_FAKE_API_KEY, account_id=_FAKE_ACCOUNT_ID)
    assert config.base_url == DEFAULT_NEW_RELIC_BASE_URL


def test_config_accepts_the_documented_eu_host() -> None:
    config = NewRelicIntegrationConfig(
        api_key=_FAKE_API_KEY,
        account_id=_FAKE_ACCOUNT_ID,
        base_url="https://api.eu.newrelic.com",
    )
    assert config.base_url == "https://api.eu.newrelic.com"


def test_config_accepts_the_documented_jp_host() -> None:
    config = NewRelicIntegrationConfig(
        api_key=_FAKE_API_KEY,
        account_id=_FAKE_ACCOUNT_ID,
        base_url="https://api.jp.newrelic.com",
    )
    assert config.base_url == "https://api.jp.newrelic.com"


@pytest.mark.parametrize(
    "base_url",
    [
        "https://attacker.example.com",
        "https://api.newrelic.com.attacker.example.com",
        "http://api.newrelic.com",  # right host, wrong scheme
    ],
)
def test_config_rejects_a_base_url_outside_the_documented_hosts(base_url: str) -> None:
    with pytest.raises(ValidationError):
        NewRelicIntegrationConfig(
            api_key=_FAKE_API_KEY, account_id=_FAKE_ACCOUNT_ID, base_url=base_url
        )

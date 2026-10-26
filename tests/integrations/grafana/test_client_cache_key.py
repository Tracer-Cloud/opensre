"""The client cache must key on credentials and TLS config, not just account+endpoint.

``get_grafana_client_from_credentials`` cached the client under
``creds_{account_id}_{endpoint}``, so after a token rotation, a changed
Basic-Auth pair, or a changed ``verify_ssl``/``ca_bundle`` the same
(account_id, endpoint) returned the stale client carrying the old credentials.
The cache key now folds in a hashed credential/TLS fingerprint, so any auth/TLS
change constructs a fresh client while an identical config still reuses one.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from integrations.grafana import client as grafana_client

_ACCOUNT_ID = "acct-1"
_ENDPOINT = "https://grafana.example.net"
_OLD_TOKEN = "old-token"
_NEW_TOKEN = "new-token"


@pytest.fixture(autouse=True)
def _clear_client_cache() -> Any:
    grafana_client._grafana_client_cache.clear()
    grafana_client._grafana_identity_versions.clear()
    yield
    grafana_client._grafana_client_cache.clear()
    grafana_client._grafana_identity_versions.clear()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grafana_client.GrafanaClient, "discover_datasource_uids", lambda _self: {})


def test_rotated_api_key_builds_new_client() -> None:
    first = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID
    )
    second = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_NEW_TOKEN, account_id=_ACCOUNT_ID
    )

    assert first is not second
    assert second.read_token == _NEW_TOKEN
    assert first.read_token == _OLD_TOKEN


def test_rotated_credential_evicts_the_stale_entry() -> None:
    """The old token's cache entry must not linger forever across rotations."""
    grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID
    )
    grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_NEW_TOKEN, account_id=_ACCOUNT_ID
    )

    assert len(grafana_client._grafana_client_cache) == 1


def test_whitespace_only_difference_shares_cache_entry() -> None:
    """The fingerprint strips before hashing to match what the built client
    ends up with: ``GrafanaAccountConfig`` inherits a wildcard
    ``StrictConfigModel`` validator that strips every string field, so an
    api_key differing only by surrounding whitespace produces the identical
    ``read_token`` on the built client either way -- fingerprinting the raw,
    unstripped value would treat these as different credentials and build a
    redundant client for no real difference.
    """
    first = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID
    )
    second = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=f" {_OLD_TOKEN} ", account_id=_ACCOUNT_ID
    )

    assert first is second
    assert first.read_token == _OLD_TOKEN


def test_identical_config_reuses_cached_client() -> None:
    first = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID
    )
    second = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID
    )

    assert first is second


def test_equivalent_endpoint_shares_cache_entry() -> None:
    """A trailing slash is normalized, so it still hits the same cache entry."""
    first = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID
    )
    second = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT + "/", api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID
    )

    assert first is second


def test_changed_verify_ssl_builds_new_client() -> None:
    first = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID, verify_ssl=True
    )
    second = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID, verify_ssl=False
    )

    assert first is not second


def test_changed_ca_bundle_builds_new_client() -> None:
    first = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID, ca_bundle="/etc/ca-a.pem"
    )
    second = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID, ca_bundle="/etc/ca-b.pem"
    )

    assert first is not second


def test_changed_basic_auth_builds_new_client() -> None:
    first = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT,
        api_key="",
        account_id=_ACCOUNT_ID,
        username="alice",
        password="secret-a",
    )
    second = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT,
        api_key="",
        account_id=_ACCOUNT_ID,
        username="alice",
        password="secret-b",
    )

    assert first is not second


def test_different_account_id_does_not_evict_the_other_accounts_client() -> None:
    """Eviction is scoped to one (account, endpoint) identity, not global."""
    other = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id="acct-other"
    )
    grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID
    )
    grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_NEW_TOKEN, account_id=_ACCOUNT_ID
    )

    assert len(grafana_client._grafana_client_cache) == 2
    unchanged = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id="acct-other"
    )
    assert unchanged is other


def test_a_slow_old_build_finishing_after_a_new_one_does_not_win_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two different credential versions build under independent gates, so
    nothing else serializes them. If an in-flight old-credential build (e.g.
    one already underway when a rotation happens) finishes after the new
    build, it must not evict the newer, still-valid client -- that would
    force the very next current-credential call to rebuild and repeat
    datasource discovery.
    """
    real_discover = grafana_client.GrafanaClient.discover_datasource_uids

    def _discover(self: Any) -> dict[str, str]:
        if self.read_token == _OLD_TOKEN:
            time.sleep(0.1)  # the old build is the slow one, so it finishes last
        return real_discover(self)

    monkeypatch.setattr(grafana_client.GrafanaClient, "discover_datasource_uids", _discover)

    results: dict[str, Any] = {}

    def _build(token: str) -> None:
        results[token] = grafana_client.get_grafana_client_from_credentials(
            endpoint=_ENDPOINT, api_key=token, account_id=_ACCOUNT_ID
        )

    old_thread = threading.Thread(target=_build, args=(_OLD_TOKEN,))
    old_thread.start()
    time.sleep(0.02)  # let the old build start (and reserve the earlier sequence) first
    new_thread = threading.Thread(target=_build, args=(_NEW_TOKEN,))
    new_thread.start()
    old_thread.join(timeout=5)
    new_thread.join(timeout=5)

    # Both callers still get their own correctly-built client...
    assert results[_OLD_TOKEN].read_token == _OLD_TOKEN
    assert results[_NEW_TOKEN].read_token == _NEW_TOKEN
    # ...but the cache holds only the newer one, so a subsequent call with the
    # current credentials reuses it instead of rebuilding.
    assert len(grafana_client._grafana_client_cache) == 1
    reused = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_NEW_TOKEN, account_id=_ACCOUNT_ID
    )
    assert reused is results[_NEW_TOKEN]

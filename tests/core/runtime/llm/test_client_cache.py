"""Unit tests for ``core.llm.internal.client_cache.LLMClientCache``.

The cache holds one client per role and clears the whole set when the
``(transport, provider)`` config key changes, so a ``/model`` switch rebuilds
every client against the new configuration. Callers ``get(role, key)`` first
(which binds/invalidates on the key) and only ``store(role, client, key)`` on a
miss, passing the key the client was built under so a config change during the
build drops the stale client instead of caching it.
"""

from __future__ import annotations

from core.llm.internal.client_cache import LLMClientCache

_KEY_A = ("sdk", "anthropic")
_KEY_B = ("sdk", "openai")


def test_get_returns_none_when_empty() -> None:
    cache = LLMClientCache()
    assert cache.get("agent", _KEY_A) is None


def test_store_then_get_returns_same_instance() -> None:
    cache = LLMClientCache()
    client = object()
    cache.get("agent", _KEY_A)  # bind the config key (miss)
    cache.store("agent", client, _KEY_A)
    assert cache.get("agent", _KEY_A) is client


def test_roles_are_cached_independently() -> None:
    cache = LLMClientCache()
    agent, reasoning = object(), object()
    cache.get("agent", _KEY_A)  # bind key
    cache.store("agent", agent, _KEY_A)
    cache.store("reasoning", reasoning, _KEY_A)
    assert cache.get("agent", _KEY_A) is agent
    assert cache.get("reasoning", _KEY_A) is reasoning


def test_config_key_change_invalidates_the_whole_cache() -> None:
    cache = LLMClientCache()
    client = object()
    cache.get("agent", _KEY_A)  # bind key
    cache.store("agent", client, _KEY_A)
    assert cache.get("agent", _KEY_A) is client  # same key still hits
    assert cache.get("agent", _KEY_B) is None  # different key clears everything


def test_store_drops_a_client_whose_config_changed_during_build() -> None:
    # Arrange: a build begins under key A (get binds A on the miss).
    cache = LLMClientCache()
    cache.get("agent", _KEY_A)

    # Act: a concurrent /model switch rebinds the cache to key B, then the
    # in-flight build tries to store its client carrying the old key A.
    cache.get("agent", _KEY_B)
    stale = object()
    cache.store("agent", stale, _KEY_A)

    # Assert: the stale client is not served under the current key.
    assert cache.get("agent", _KEY_B) is None


def test_clear_empties_cache_and_resets_config_key() -> None:
    cache = LLMClientCache()
    first = object()
    cache.get("agent", _KEY_A)
    cache.store("agent", first, _KEY_A)

    cache.clear()

    # After clear the role rebuilds (miss), and the config key is reset so a
    # fresh store under the same key is not wiped by a stale-key comparison.
    assert cache.get("agent", _KEY_A) is None
    second = object()
    cache.store("agent", second, _KEY_A)
    assert cache.get("agent", _KEY_A) is second


def test_none_config_key_is_handled() -> None:
    cache = LLMClientCache()
    client = object()
    cache.store("agent", client, None)  # config key starts as None
    assert cache.get("agent", None) is client

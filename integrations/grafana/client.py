"""Unified Grafana Cloud client composed from mixins."""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass, field

from config.constants.grafana import (
    GRAFANA_CA_BUNDLE_ENV,
    GRAFANA_INSTANCE_URL_ENV,
    GRAFANA_READ_TOKEN_ENV,
    GRAFANA_VERIFY_SSL_ENV,
)
from config.grafana_cloud import DEFAULT_INSTANCE_URL, get_datasource_uids
from integrations.grafana.base import GrafanaClientBase
from integrations.grafana.config import GrafanaAccountConfig
from integrations.grafana.loki import LokiMixin
from integrations.grafana.mimir import MimirMixin
from integrations.grafana.tempo import TempoMixin

logger = logging.getLogger(__name__)

# Successful clients only. Transport failures are NOT cached here — a process-
# wide failure entry would pin a host "dead" across /new and after recovery.
# Concurrent gather tools that miss the success cache share one in-flight build
# via ``_BuildGate`` so they do not each wait out the connect timeout.
_grafana_client_cache: dict[str, GrafanaClient] = {}
#: Datasource discovery is one network round trip per account. An investigation
#: queries Mimir, Loki, Tempo and alert rules concurrently, so without this the
#: first callers all miss the empty cache and each runs its own discovery — and
#: against an unreachable Grafana each one waits out the full connect timeout.
_grafana_client_lock = threading.Lock()


@dataclass
class _BuildGate:
    """One shared build attempt for concurrent callers of the same cache key."""

    done: threading.Event = field(default_factory=threading.Event)
    client: GrafanaClient | None = None
    error: BaseException | None = None
    #: Assigned when this gate becomes the build leader; see
    #: ``_grafana_identity_versions`` for why this exists.
    sequence: int = 0


_grafana_build_inflight: dict[str, _BuildGate] = {}

#: Monotonic counter assigned to each build leader, and the highest sequence
#: number successfully cached per (account, endpoint) identity. Two different
#: credential versions of the same identity build under different cache keys
#: with independent gates, so nothing else serializes them: without this, an
#: old-credential build that happens to finish after a newer one would win the
#: eviction race and leave the stale client cached. A build only wins the
#: cache slot when its sequence is not older than the identity's current
#: winner; a build that loses the race still returns its own correctly-built
#: client to its caller -- it just isn't cached for reuse.
_grafana_next_sequence = 0
_grafana_identity_versions: dict[str, int] = {}

#: Hex chars of the credential/TLS fingerprint folded into the cache key.
#: 16 hex chars = 64 bits of SHA-256 -- collision-safe for the handful of live
#: (account, endpoint) configs a process holds, while keeping the key short.
#: The raw secrets never appear in the key itself (it can leak into logs or
#: reprs); only this hash does.
_FINGERPRINT_HEX_LEN = 16
#: Separates a cache key's ``(account, endpoint)`` identity from its trailing
#: fingerprint. Two keys sharing the identity but differing in fingerprint are
#: different credential versions of the same account+endpoint -- the older one
#: is evicted on rotation.
_FINGERPRINT_SEP = "#"


def _cache_key_identity(*, account_id: str, endpoint: str) -> str:
    """Identity shared by every credential version of one (account, endpoint).

    Normalizes the endpoint the same way ``GrafanaAccountConfig`` does
    (``strip().rstrip("/")``) so equivalent-but-differently-typed endpoints
    map to the same identity.
    """
    normalized_endpoint = endpoint.strip().rstrip("/")
    return f"creds_{account_id}_{normalized_endpoint}"


def _cache_key(
    *,
    endpoint: str,
    api_key: str,
    account_id: str,
    username: str,
    password: str,
    verify_ssl: bool,
    ca_bundle: str,
) -> str:
    """Build a cache key that changes whenever auth or TLS config changes.

    Keying only on (account_id, endpoint) let a rotated token, changed Basic
    Auth, or a changed verify_ssl/ca_bundle silently reuse the previously
    cached client until process restart. The credential tuple is hashed, never
    embedded in plaintext, so a rotation yields a fresh key without leaking the
    secret into the key string.

    Every string component is stripped before hashing to match what the built
    client actually ends up with: ``GrafanaAccountConfig`` (a
    ``StrictConfigModel``) inherits a wildcard ``field_validator("*")`` that
    strips every string field, ``read_token``/``username``/``password``/
    ``ca_bundle`` included -- not stripping here would treat two inputs the
    client normalizes to the identical value as different credentials,
    building a redundant client and cache entry for no real difference.
    """
    fingerprint = hashlib.sha256(
        repr(
            (api_key.strip(), username.strip(), password.strip(), verify_ssl, ca_bundle.strip())
        ).encode()
    ).hexdigest()[:_FINGERPRINT_HEX_LEN]
    return f"{_cache_key_identity(account_id=account_id, endpoint=endpoint)}{_FINGERPRINT_SEP}{fingerprint}"


class GrafanaClient(LokiMixin, TempoMixin, MimirMixin, GrafanaClientBase):
    """Unified client for querying Grafana Cloud Loki, Tempo, and Mimir."""

    pass


def get_grafana_client() -> GrafanaClient:
    """Create a Grafana client from environment variables."""
    import os

    from config.llm_credentials import resolve_env_credential

    return get_grafana_client_from_credentials(
        endpoint=os.getenv(GRAFANA_INSTANCE_URL_ENV, DEFAULT_INSTANCE_URL),
        api_key=resolve_env_credential(GRAFANA_READ_TOKEN_ENV),
        account_id="env_default",
        verify_ssl=os.getenv(GRAFANA_VERIFY_SSL_ENV, "true").strip().lower() != "false",
        ca_bundle=os.getenv(GRAFANA_CA_BUNDLE_ENV, "").strip(),
    )


def get_grafana_client_from_credentials(
    endpoint: str,
    api_key: str,
    account_id: str = "user_integration",
    username: str = "",
    password: str = "",
    verify_ssl: bool = True,
    ca_bundle: str = "",
) -> GrafanaClient:
    """Create a Grafana client from integration credentials."""
    cache_key = _cache_key(
        endpoint=endpoint,
        api_key=api_key,
        account_id=account_id,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
        ca_bundle=ca_bundle,
    )
    cached = _grafana_client_cache.get(cache_key)
    if cached is not None:
        return cached

    gate: _BuildGate | None = None
    leader = False
    with _grafana_client_lock:
        cached = _grafana_client_cache.get(cache_key)
        if cached is not None:
            return cached
        gate = _grafana_build_inflight.get(cache_key)
        if gate is None:
            global _grafana_next_sequence
            _grafana_next_sequence += 1
            gate = _BuildGate(sequence=_grafana_next_sequence)
            _grafana_build_inflight[cache_key] = gate
            leader = True

    assert gate is not None
    if not leader:
        gate.done.wait()
        if gate.client is not None:
            return gate.client
        if gate.error is not None:
            raise gate.error
        # Leader finished without client or error (should not happen); retry.
        return get_grafana_client_from_credentials(
            endpoint=endpoint,
            api_key=api_key,
            account_id=account_id,
            username=username,
            password=password,
            verify_ssl=verify_ssl,
            ca_bundle=ca_bundle,
        )

    try:
        client = _build_and_cache_client(
            cache_key=cache_key,
            sequence=gate.sequence,
            endpoint=endpoint,
            api_key=api_key,
            account_id=account_id,
            username=username,
            password=password,
            verify_ssl=verify_ssl,
            ca_bundle=ca_bundle,
        )
        gate.client = client
        return client
    except BaseException as exc:
        gate.error = exc
        raise
    finally:
        gate.done.set()
        with _grafana_client_lock:
            if _grafana_build_inflight.get(cache_key) is gate:
                del _grafana_build_inflight[cache_key]


def _build_and_cache_client(
    *,
    cache_key: str,
    sequence: int,
    endpoint: str,
    api_key: str,
    account_id: str,
    username: str,
    password: str,
    verify_ssl: bool,
    ca_bundle: str,
) -> GrafanaClient:
    """Discover datasources once and cache the resulting client.

    Prefer live discovery; when a UID is missing, fall back to
    ``GRAFANA_*_DATASOURCE_UID`` / Grafana Cloud defaults so env-configured
    installs still work when auto-discovery is incomplete.
    """
    config = GrafanaAccountConfig(
        account_id=account_id,
        instance_url=endpoint.rstrip("/"),
        read_token=api_key,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
        ca_bundle=ca_bundle,
    )
    client = GrafanaClient(config=config)

    discovered = client.discover_datasource_uids() or {}
    fallback_loki, fallback_tempo, fallback_mimir = get_datasource_uids()
    loki_uid = discovered.get("loki_uid") or fallback_loki
    tempo_uid = discovered.get("tempo_uid") or fallback_tempo
    mimir_uid = discovered.get("mimir_uid") or fallback_mimir

    if loki_uid or tempo_uid or mimir_uid:
        config = GrafanaAccountConfig(
            account_id=account_id,
            instance_url=endpoint.rstrip("/"),
            read_token=api_key,
            username=username,
            password=password,
            verify_ssl=verify_ssl,
            ca_bundle=ca_bundle,
            loki_datasource_uid=loki_uid,
            tempo_datasource_uid=tempo_uid,
            mimir_datasource_uid=mimir_uid,
        )
        client = GrafanaClient(config=config)
        logger.info(
            "[grafana] Client ready for account_id=%s with datasource UIDs: "
            "loki=%s tempo=%s mimir=%s (discovered=%s)",
            account_id,
            config.loki_datasource_uid,
            config.tempo_datasource_uid,
            config.mimir_datasource_uid,
            bool(discovered),
        )
    else:
        logger.warning(
            "[grafana] Could not resolve datasource UIDs for account_id=%s — queries will fail",
            account_id,
        )

    identity = _cache_key_identity(account_id=account_id, endpoint=endpoint)
    with _grafana_client_lock:
        # Two different credential versions of the same identity build under
        # different cache keys with independent gates, so nothing else
        # serializes them. Only cache this build if no newer-sequenced build
        # for the same identity has already won -- otherwise a slow rebuild
        # for since-rotated (possibly now-invalid) credentials could finish
        # after the current one and silently evict it.
        if sequence >= _grafana_identity_versions.get(identity, 0):
            # Evict superseded credential versions of the same (account,
            # endpoint): on rotation a fresh fingerprint would otherwise
            # leave the old token/password cached forever.
            identity_prefix = f"{identity}{_FINGERPRINT_SEP}"
            for stale_key in [
                key
                for key in _grafana_client_cache
                if key != cache_key and key.startswith(identity_prefix)
            ]:
                del _grafana_client_cache[stale_key]
            _grafana_client_cache[cache_key] = client
            _grafana_identity_versions[identity] = sequence
    return client

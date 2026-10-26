"""LLM-auth and secret-storage StrEnums keep their persisted string values.

The values below are written to on-disk auth metadata, analytics events, and
env vars, so they are a compatibility contract: renaming a *member* is safe, but
changing its ``.value`` breaks previously saved records. These tests pin the
values and the round-trip so a drive-by rename cannot silently move them.
"""

from __future__ import annotations

from config.llm_auth.credentials import CredentialSource
from config.llm_auth.provider_catalog import CredentialKind
from config.secrets.backend import SecretTier


def test_credential_kind_values() -> None:
    assert {kind.value for kind in CredentialKind} == {
        "api_key",
        "cli",
        "ambient",
        "local",
        "subscription",
    }
    assert CredentialKind("subscription") is CredentialKind.SUBSCRIPTION
    assert CredentialKind("local") is CredentialKind.LOCAL
    # StrEnum stays str-compatible for the many literal comparisons still in use.
    assert CredentialKind.API_KEY == "api_key"


def test_secret_tier_values() -> None:
    assert {tier.value for tier in SecretTier} == {"env", "fallback", "none"}
    assert SecretTier("fallback") is SecretTier.FALLBACK
    assert SecretTier.ENV == "env"


def test_credential_source_values() -> None:
    assert {source.value for source in CredentialSource} == {
        "env",
        "fallback",
        "metadata",
        "cli",
        "ambient",
        "local",
        "none",
        "unknown",
        "subscription",
    }
    assert CredentialSource("subscription") is CredentialSource.SUBSCRIPTION
    assert CredentialSource("fallback") is CredentialSource.FALLBACK
    assert CredentialSource.NONE == "none"

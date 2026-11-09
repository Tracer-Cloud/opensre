"""One identity-policy store shared by every chat transport.

Each transport carried its own copy of load/save/persist — four byte-identical
implementations differing only in the platform string. The transports AGENTS.md
rule ("anything two transports need belongs in gateway.core") existed; the
copies violated it, and a fix to one drifted from the other three.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from gateway.core.middleware.identity_policy import (
    load_identity_policy,
    persist_policy_if_needed,
    save_identity_policy,
)
from integrations.messaging_security import MessagingIdentityPolicy


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from config.constants import paths

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)


def test_load_returns_a_permissive_default_when_nothing_is_stored() -> None:
    record, policy = load_identity_policy("telegram")

    assert record is None
    assert policy.inbound_enabled is True
    assert policy.allowed_user_ids == []


def test_save_then_load_round_trips_per_platform() -> None:
    """Two platforms' policies must not bleed into each other."""
    record, policy = load_identity_policy("telegram")
    policy.allowed_user_ids = ["tg-1"]
    save_identity_policy("telegram", record, policy)

    _, reloaded = load_identity_policy("telegram")
    _, other = load_identity_policy("buzz")

    assert reloaded.allowed_user_ids == ["tg-1"]
    assert other.allowed_user_ids == []


@dataclass(frozen=True)
class _Decision:
    """Duck-typed stand-in for any transport's inbound decision."""

    persist_policy: bool
    updated_policy: MessagingIdentityPolicy | None


def test_persist_if_needed_writes_only_when_asked() -> None:
    updated = MessagingIdentityPolicy(inbound_enabled=True)
    updated.allowed_user_ids = ["u-9"]

    persist_policy_if_needed("discord", _Decision(persist_policy=False, updated_policy=updated))
    _, unchanged = load_identity_policy("discord")
    assert unchanged.allowed_user_ids == []

    persist_policy_if_needed("discord", _Decision(persist_policy=True, updated_policy=None))
    _, still_unchanged = load_identity_policy("discord")
    assert still_unchanged.allowed_user_ids == []

    persist_policy_if_needed("discord", _Decision(persist_policy=True, updated_policy=updated))
    _, persisted = load_identity_policy("discord")
    assert persisted.allowed_user_ids == ["u-9"]


def test_save_preserves_existing_instance_identity() -> None:
    """Persisting the policy must not rename or re-tag the stored integration."""
    from integrations.store import get_integration, upsert_instance

    upsert_instance(
        "telegram",
        {"name": "prod-bot", "tags": {"env": "prod"}, "credentials": {"token": "t"}},
    )
    record, policy = load_identity_policy("telegram")
    policy.allowed_user_ids = ["tg-2"]

    save_identity_policy("telegram", record, policy)

    stored: dict[str, Any] = get_integration("telegram") or {}
    instance = stored["instances"][0]
    assert instance["name"] == "prod-bot"
    assert instance["tags"] == {"env": "prod"}
    credentials = stored.get("credentials") or instance.get("credentials") or {}
    assert credentials["identity_policy"]["allowed_user_ids"] == ["tg-2"]
    assert credentials["token"] == "t"

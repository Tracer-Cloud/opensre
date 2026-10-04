from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from gateway.transports.telegram.owner_seed import (
    private_chat_user_id,
    seed_owner_from_private_chat,
)
from integrations.messaging_security import MessagingIdentityPolicy

_STORE = "gateway.core.middleware.identity_policy"


class _Store:
    """In-memory Telegram integration record behind the identity-policy store."""

    def __init__(self, credentials: dict[str, Any] | None) -> None:
        self.record = None if credentials is None else {"id": "r1", "credentials": credentials}

    def get(self, _platform: str) -> dict[str, Any] | None:
        return self.record

    def upsert(self, _platform: str, instance: dict[str, Any], **_kw: Any) -> None:
        self.record = {"id": "r1", "credentials": instance["credentials"]}

    def policy(self) -> MessagingIdentityPolicy:
        assert self.record is not None
        return MessagingIdentityPolicy.model_validate(self.record["credentials"]["identity_policy"])


@pytest.fixture
def store(request: pytest.FixtureRequest) -> Iterator[_Store]:
    fake = _Store(request.param)
    with (
        patch(f"{_STORE}.get_integration", side_effect=fake.get),
        patch(f"{_STORE}.upsert_instance", side_effect=fake.upsert),
    ):
        yield fake


def _empty_policy_after_failed_pair() -> dict[str, Any]:
    return MessagingIdentityPolicy(inbound_enabled=True).model_dump(mode="json")


@pytest.mark.parametrize(
    "store",
    [
        {"default_chat_id": "123456789"},
        # A stray "/pair <code>" before any pairing persists an empty policy.
        {"default_chat_id": "123456789", "identity_policy": _empty_policy_after_failed_pair()},
    ],
    indirect=True,
)
def test_private_chat_is_seeded_as_owner(store: _Store) -> None:
    seed_owner_from_private_chat(env_allowed_user_ids=[])
    policy = store.policy()
    assert policy.allowed_user_ids == ["123456789"]
    assert policy.owner_seeded is True


@pytest.mark.parametrize("store", [{"default_chat_id": "123456789"}], indirect=True)
def test_revoked_owner_is_not_reseeded(store: _Store) -> None:
    seed_owner_from_private_chat(env_allowed_user_ids=[])
    revoked = store.policy()
    revoked.allowed_user_ids = []
    assert store.record is not None
    store.record["credentials"]["identity_policy"] = revoked.model_dump(mode="json")

    seed_owner_from_private_chat(env_allowed_user_ids=[])

    assert store.policy().allowed_user_ids == []


@pytest.mark.parametrize("store", [{"default_chat_id": "123456789"}], indirect=True)
def test_env_allowlist_skips_seeding(store: _Store) -> None:
    seed_owner_from_private_chat(env_allowed_user_ids=["42"])
    assert store.record is not None
    assert "identity_policy" not in store.record["credentials"]


@pytest.mark.parametrize("chat_id", ["-1001234567890", "-42", "@channel", "", "0", None])
def test_group_channel_and_blank_chats_are_not_users(chat_id: object) -> None:
    assert private_chat_user_id({"default_chat_id": chat_id}) == ""

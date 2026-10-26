from __future__ import annotations

from unittest.mock import patch

import pytest

from gateway.core.middleware.identity_policy import persist_policy_if_needed
from gateway.transports.telegram.inbound_security import (
    enforce_inbound_telegram_message_security,
)
from integrations.messaging_security import MessagingIdentityPolicy

_SECURITY = "gateway.transports.telegram.inbound_security"


@pytest.fixture
def mock_integration_store():
    with (
        patch("gateway.core.middleware.identity_policy.get_integration", return_value=None),
        patch("gateway.core.middleware.identity_policy.upsert_instance") as upsert,
    ):
        yield upsert


@pytest.mark.usefixtures("mock_integration_store")
def test_help_is_not_agent_turn() -> None:
    decision = enforce_inbound_telegram_message_security(
        user_id="42",
        chat_id="42",
        text="/help",
        env_allowed_user_ids=["42"],
    )
    assert decision.allowed is False
    assert "OpenSRE Telegram gateway" in decision.reply_text


@pytest.mark.usefixtures("mock_integration_store")
def test_unauthorized_user_gets_reason() -> None:
    decision = enforce_inbound_telegram_message_security(
        user_id="99",
        chat_id="99",
        text="hello",
        env_allowed_user_ids=["42"],
    )
    assert decision.allowed is False
    assert decision.reply_text


def test_pair_attempt_persists_policy(mock_integration_store: pytest.MonkeyPatch) -> None:
    policy = MessagingIdentityPolicy(
        inbound_enabled=True,
        pairing_secret_hash="abc",
    )
    with (
        patch(
            f"{_SECURITY}.load_identity_policy",
            return_value=(None, policy),
        ),
        patch(
            f"{_SECURITY}.complete_pairing",
            return_value=(True, "Pairing successful!"),
        ),
    ):
        decision = enforce_inbound_telegram_message_security(
            user_id="42",
            chat_id="42",
            text="/pair CODE",
            env_allowed_user_ids=[],
        )
    assert decision.persist_policy is True
    persist_policy_if_needed("telegram", decision)
    mock_integration_store.assert_called_once()


@pytest.mark.usefixtures("mock_integration_store")
def test_unauthorized_user_cannot_rotate_session() -> None:
    decision = enforce_inbound_telegram_message_security(
        user_id="99",
        chat_id="99",
        text="/new",
        env_allowed_user_ids=["42"],
    )
    assert decision.allowed is False
    assert decision.reply_text
    assert decision.reply_text != "__ROTATE_SESSION__"


@pytest.mark.usefixtures("mock_integration_store")
def test_authorized_user_can_rotate_session() -> None:
    decision = enforce_inbound_telegram_message_security(
        user_id="42",
        chat_id="42",
        text="/new",
        env_allowed_user_ids=["42"],
    )
    assert decision.allowed is True
    assert decision.reply_text == "__ROTATE_SESSION__"


def _connected_record(chat_id: str, **policy: object) -> dict[str, object]:
    credentials: dict[str, object] = {"bot_token": "t", "default_chat_id": chat_id}
    if policy:
        credentials["identity_policy"] = MessagingIdentityPolicy(
            inbound_enabled=True, **policy
        ).model_dump(mode="json")
    return {"id": "r1", "credentials": credentials}


@pytest.mark.parametrize(
    ("chat_id", "user_id", "allowed"),
    [
        ("123456789", "123456789", True),  # connecting a private chat authorizes it
        ("123456789", "99", False),  # anyone else still needs to be allowed
        ("-1001234567890", "1001234567890", False),  # group/channel IDs never name a user
    ],
)
def test_connected_private_chat_is_authorized_without_pairing(
    chat_id: str, user_id: str, allowed: bool
) -> None:
    with (
        patch(
            "gateway.core.middleware.identity_policy.get_integration",
            return_value=_connected_record(chat_id),
        ),
        patch("gateway.core.middleware.identity_policy.upsert_instance"),
    ):
        decision = enforce_inbound_telegram_message_security(
            user_id=user_id, chat_id=user_id, text="hello", env_allowed_user_ids=[]
        )
    assert decision.allowed is allowed


def test_connected_chat_respects_allowed_chat_ids() -> None:
    record = _connected_record("123456789", allowed_chat_ids=["-100"])
    with (
        patch("gateway.core.middleware.identity_policy.get_integration", return_value=record),
        patch("gateway.core.middleware.identity_policy.upsert_instance"),
    ):
        decision = enforce_inbound_telegram_message_security(
            user_id="123456789", chat_id="123456789", text="hello", env_allowed_user_ids=[]
        )
    assert decision.allowed is False

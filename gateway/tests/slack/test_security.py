from __future__ import annotations

from unittest.mock import patch

import pytest

from gateway.core.middleware.identity_policy import persist_policy_if_needed
from gateway.transports.slack.processing.security import (
    enforce_inbound_slack_message_security,
)
from integrations.messaging_security import MessagingIdentityPolicy

_SECURITY = "gateway.transports.slack.processing.security"


@pytest.fixture
def mock_integration_store():
    with (
        patch("gateway.core.middleware.identity_policy.get_integration", return_value=None),
        patch("gateway.core.middleware.identity_policy.upsert_instance") as upsert,
    ):
        yield upsert


@pytest.mark.usefixtures("mock_integration_store")
def test_allowlisted_user_is_authorized() -> None:
    decision = enforce_inbound_slack_message_security(
        user_id="U111",
        channel_id="C222",
        text="check disk usage",
        env_allowed_user_ids=["U111", "U999"],
    )
    assert decision.allowed is True


@pytest.mark.usefixtures("mock_integration_store")
def test_unlisted_user_is_denied() -> None:
    decision = enforce_inbound_slack_message_security(
        user_id="U666",
        channel_id="C222",
        text="check disk usage",
        env_allowed_user_ids=["U111"],
    )
    assert decision.allowed is False
    # Denial reasons stay in the audit log; the channel reply carries nothing.
    assert decision.reply_text == ""


@pytest.mark.usefixtures("mock_integration_store")
def test_empty_allowlist_denied_without_open_flag() -> None:
    decision = enforce_inbound_slack_message_security(
        user_id="Uanybody",
        channel_id="C222",
        text="hello",
        env_allowed_user_ids=[],
        allow_open_workspace=False,
    )
    assert decision.allowed is False
    assert decision.reply_text == ""


@pytest.mark.usefixtures("mock_integration_store")
def test_empty_allowlist_open_when_explicitly_enabled() -> None:
    decision = enforce_inbound_slack_message_security(
        user_id="Uanybody",
        channel_id="C222",
        text="hello",
        env_allowed_user_ids=[],
        allow_open_workspace=True,
    )
    assert decision.allowed is True


@pytest.mark.usefixtures("mock_integration_store")
def test_help_is_not_agent_turn() -> None:
    decision = enforce_inbound_slack_message_security(
        user_id="U111",
        channel_id="C222",
        text="/help",
        env_allowed_user_ids=["U111"],
    )
    assert decision.allowed is False
    assert "OpenSRE Slack gateway" in decision.reply_text


def test_pair_attempt_persists_policy(mock_integration_store) -> None:
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
        decision = enforce_inbound_slack_message_security(
            user_id="U111",
            channel_id="C222",
            text="/pair CODE",
            env_allowed_user_ids=[],
        )
    assert decision.persist_policy is True
    persist_policy_if_needed("slack", decision)
    mock_integration_store.assert_called_once()


@pytest.mark.usefixtures("mock_integration_store")
def test_pair_under_open_workspace_keeps_stored_pairing_state() -> None:
    stored = MessagingIdentityPolicy(
        inbound_enabled=True,
        require_dm_pairing=True,
        pairing_secret_hash="abc",
    )
    with (
        patch(f"{_SECURITY}.load_identity_policy", return_value=(None, stored)),
        patch(f"{_SECURITY}.complete_pairing", return_value=(True, "Pairing successful!")) as pair,
    ):
        decision = enforce_inbound_slack_message_security(
            user_id="U111",
            channel_id="C222",
            text="/pair CODE",
            env_allowed_user_ids=[],
            allow_open_workspace=True,
        )

    # Pairing must run against the stored policy, and persisting the decision
    # must not overwrite the store with a synthetic open-workspace policy.
    assert pair.call_args.kwargs["policy"] is stored
    assert decision.updated_policy is stored
    assert decision.updated_policy.pairing_secret_hash == "abc"


@pytest.mark.usefixtures("mock_integration_store")
def test_unauthorized_user_cannot_rotate_session() -> None:
    decision = enforce_inbound_slack_message_security(
        user_id="U666",
        channel_id="C222",
        text="/new",
        env_allowed_user_ids=["U111"],
    )
    assert decision.allowed is False
    assert decision.reply_text != "__ROTATE_SESSION__"


@pytest.mark.usefixtures("mock_integration_store")
def test_authorized_user_can_rotate_session() -> None:
    decision = enforce_inbound_slack_message_security(
        user_id="U111",
        channel_id="C222",
        text="/new",
        env_allowed_user_ids=["U111"],
    )
    assert decision.allowed is True
    assert decision.reply_text == "__ROTATE_SESSION__"

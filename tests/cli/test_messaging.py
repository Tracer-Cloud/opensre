"""Tests for the `opensre messaging` CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from integrations.messaging_security import verify_pairing_code
from surfaces.cli.commands.messaging import messaging


@pytest.fixture()
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the integration store to a temp directory."""
    store_path = tmp_path / "integrations.json"
    store_path.write_text(json.dumps({"version": 2, "integrations": []}))
    monkeypatch.setattr("integrations.store.STORE_PATH", store_path)
    return store_path


class TestMessagingPairCommand:
    def test_pair_generates_code(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(messaging, ["pair", "--platform", "telegram"])
        assert result.exit_code == 0
        assert "Pairing code generated" in result.output

    def test_pair_stores_hash_in_integration(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(messaging, ["pair", "--platform", "telegram"])
        assert result.exit_code == 0

        # Read back the store and verify the hash was persisted
        data = json.loads(_isolated_store.read_text())
        integrations = data["integrations"]
        assert len(integrations) == 1
        record = integrations[0]
        assert record["service"] == "telegram"
        creds = record["instances"][0]["credentials"]
        policy_data = creds["identity_policy"]
        assert policy_data["pairing_secret_hash"] is not None
        assert policy_data["inbound_enabled"] is True
        assert policy_data["require_dm_pairing"] is True

    def test_pair_code_is_verifiable(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(messaging, ["pair", "--platform", "discord"])
        assert result.exit_code == 0

        # Extract the code from output (it's between bold markers)
        lines = result.output.strip().split("\n")
        code_line = [line for line in lines if len(line.strip()) == 6 and line.strip().isalnum()]
        assert len(code_line) == 1
        code = code_line[0].strip()

        # Verify the stored hash matches
        data = json.loads(_isolated_store.read_text())
        creds = data["integrations"][0]["instances"][0]["credentials"]
        stored_hash = creds["identity_policy"]["pairing_secret_hash"]
        assert verify_pairing_code(code, stored_hash)

    def test_pair_requires_platform(self) -> None:
        runner = CliRunner()
        result = runner.invoke(messaging, ["pair"])
        assert result.exit_code != 0
        assert "Missing option" in result.output or "required" in result.output.lower()


class TestMessagingAllowCommand:
    def test_allow_adds_user(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(messaging, ["allow", "--platform", "telegram", "--user-id", "12345"])
        assert result.exit_code == 0
        assert "Added user 12345" in result.output

        data = json.loads(_isolated_store.read_text())
        creds = data["integrations"][0]["instances"][0]["credentials"]
        assert "12345" in creds["identity_policy"]["allowed_user_ids"]

    def test_allow_duplicate_user_warns(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        runner.invoke(messaging, ["allow", "--platform", "slack", "--user-id", "U001"])
        result = runner.invoke(messaging, ["allow", "--platform", "slack", "--user-id", "U001"])
        assert result.exit_code == 0
        assert "already in the allowed list" in result.output

    def test_allow_rejects_at_handle(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            messaging, ["allow", "--platform", "telegram", "--user-id", "@opensre_yauhen_bot"]
        )
        assert result.exit_code != 0
        assert "@username" in result.output
        # Nothing should have been persisted for a rejected id.
        data = json.loads(_isolated_store.read_text())
        assert data["integrations"] == []

    def test_allow_rejects_non_numeric_telegram_id(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(messaging, ["allow", "--platform", "telegram", "--user-id", "user1"])
        assert result.exit_code != 0
        assert "numeric" in result.output

    def test_allow_accepts_numeric_telegram_id(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            messaging, ["allow", "--platform", "telegram", "--user-id", " 6514715683 "]
        )
        assert result.exit_code == 0
        assert "Added user 6514715683" in result.output  # normalized (stripped)

    def test_allow_accepts_slack_native_id(self, _isolated_store: Path) -> None:
        # Non-Telegram platforms keep their native id shape (Slack U..., etc.);
        # only the @handle guard applies.
        runner = CliRunner()
        result = runner.invoke(messaging, ["allow", "--platform", "slack", "--user-id", "U12345"])
        assert result.exit_code == 0
        assert "Added user U12345" in result.output


class TestMessagingRevokeCommand:
    def test_revoke_removes_user(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        # First add a user
        runner.invoke(messaging, ["allow", "--platform", "telegram", "--user-id", "99"])
        # Then revoke
        result = runner.invoke(messaging, ["revoke", "--platform", "telegram", "--user-id", "99"])
        assert result.exit_code == 0
        assert "Removed user 99" in result.output

        data = json.loads(_isolated_store.read_text())
        creds = data["integrations"][0]["instances"][0]["credentials"]
        assert "99" not in creds["identity_policy"]["allowed_user_ids"]

    def test_revoke_nonexistent_user_warns(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(messaging, ["revoke", "--platform", "discord", "--user-id", "ghost"])
        assert result.exit_code == 0
        assert "not in the allowed list" in result.output


class TestMessagingStatusCommand:
    def test_status_no_integration(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(messaging, ["status", "--platform", "telegram"])
        assert result.exit_code == 0
        assert "No telegram integration configured" in result.output

    def test_status_with_configured_integration(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        # Set up a user first (Telegram ids are numeric)
        runner.invoke(messaging, ["allow", "--platform", "telegram", "--user-id", "6514715683"])
        result = runner.invoke(messaging, ["status", "--platform", "telegram"])
        assert result.exit_code == 0
        assert "Inbound enabled" in result.output
        assert "6514715683" in result.output

    def test_status_json_unconfigured(self, _isolated_store: Path) -> None:
        from infrastructure.process.runtime_flags import (
            configure_runtime_flags,
            reset_runtime_flags,
        )

        reset_runtime_flags()
        configure_runtime_flags(json=True)
        try:
            runner = CliRunner()
            result = runner.invoke(messaging, ["status", "--platform", "telegram"])
            assert result.exit_code == 0, result.output
            payload = json.loads(result.output)
            assert payload["platform"] == "telegram"
            assert payload["configured"] is False
            assert payload["allowed_user_ids"] == []
            assert payload["pairing_pending"] is False
            assert payload["inbound_enabled"] is False
        finally:
            reset_runtime_flags()

    def test_status_json_configured(self, _isolated_store: Path) -> None:
        from infrastructure.process.runtime_flags import (
            configure_runtime_flags,
            reset_runtime_flags,
        )

        reset_runtime_flags()
        runner = CliRunner()
        runner.invoke(messaging, ["allow", "--platform", "telegram", "--user-id", "6514715683"])
        configure_runtime_flags(json=True)
        try:
            result = runner.invoke(messaging, ["status", "--platform", "telegram"])
            assert result.exit_code == 0, result.output
            payload = json.loads(result.output)
            assert payload["platform"] == "telegram"
            assert payload["configured"] is True
            assert payload["inbound_enabled"] is True
            assert payload["require_dm_pairing"] is True
            assert payload["pairing_pending"] is False
            assert payload["allowed_user_ids"] == ["6514715683"]
            assert payload["allowed_chat_ids"] == []
            assert isinstance(payload["rejection_behavior"], str)
        finally:
            reset_runtime_flags()


class TestMessagingGroupNoSubcommand:
    def test_bare_group_shows_help_and_exits_zero(self) -> None:
        # A bare group is a help request, not an error — must exit 0 (not Click's 2)
        # so the interactive shell doesn't surface a scary "non-zero code" line.
        runner = CliRunner()
        result = runner.invoke(messaging, [])
        assert result.exit_code == 0
        assert "Commands:" in result.output
        for sub in ("allow", "pair", "revoke", "status"):
            assert sub in result.output

    def test_pair_message_points_to_platform_not_shell(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(messaging, ["pair", "--platform", "telegram"])
        assert result.exit_code == 0
        assert "not this shell" in result.output
        assert "/pair" in result.output
        assert "gateway" in result.output.lower()

"""Persisted remote-sync setup (config.yml section)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from config.constants import paths as paths_mod
from config.constants.filestorage import (
    BLOB_READ_WRITE_TOKEN_ENV,
    REMOTE_SYNC_BUCKET_ENV,
    REMOTE_SYNC_ENV,
    REMOTE_SYNC_PREFIX_ENV,
    REMOTE_SYNC_PROFILE_ENV,
    REMOTE_SYNC_PROVIDER_ENV,
    REMOTE_SYNC_REGION_ENV,
)
from config.local_settings import LocalSettingsError
from infrastructure.filestorage import setup as setup_mod
from infrastructure.filestorage.config import RemoteSyncConfig, load_remote_sync_config
from infrastructure.filestorage.errors import RemoteSyncConfigError
from infrastructure.filestorage.messages import format_setup_lines
from infrastructure.filestorage.providers import credential_hint_for_provider
from infrastructure.filestorage.setup import (
    RemoteSyncSetupRequest,
    disable_remote_sync,
    save_remote_sync_settings,
)


def _on_disk_section(tmp_path: Path) -> dict[str, Any]:
    return yaml.safe_load((tmp_path / "config.yml").read_text(encoding="utf-8"))["remote_sync"]


# Every name the loader consults. Environment beats stored config, so any one of
# these left set reaches the assertions instead of the fixture's value.
_REMOTE_SYNC_ENV_NAMES = (
    REMOTE_SYNC_ENV,
    REMOTE_SYNC_PROVIDER_ENV,
    REMOTE_SYNC_BUCKET_ENV,
    REMOTE_SYNC_PREFIX_ENV,
    REMOTE_SYNC_REGION_ENV,
    REMOTE_SYNC_PROFILE_ENV,
    BLOB_READ_WRITE_TOKEN_ENV,
)


@pytest.fixture(autouse=True)
def _clear_remote_sync_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep these tests off the developer's own remote-sync settings.

    ``bootstrap_opensre_env_once`` loads the repo ``.env``, so a machine that
    has ever run remote-sync setup exports a real bucket, prefix, region and
    profile into every test process. Clearing only the on/off switch left the
    rest of them winning over the values under test.
    """
    for name in _REMOTE_SYNC_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_save_writes_remote_sync_section(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)

    config = save_remote_sync_settings(
        RemoteSyncSetupRequest(
            bucket="opensre-remote-sync",
            provider="vercel",
            prefix="opensre",
        )
    )
    assert config.provider == "vercel"
    assert config.bucket == "opensre-remote-sync"

    on_disk = _on_disk_section(tmp_path)
    assert on_disk["enabled"] is True
    assert on_disk["provider"] == "vercel"
    assert on_disk["bucket"] == "opensre-remote-sync"

    loaded = load_remote_sync_config()
    assert loaded is not None
    assert loaded.provider == "vercel"
    assert loaded.bucket == "opensre-remote-sync"


def test_save_supplies_exactly_the_six_non_secret_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)

    config = save_remote_sync_settings(
        RemoteSyncSetupRequest(bucket=" My-Bucket ", provider=" GCS ")
    )

    assert _on_disk_section(tmp_path) == {
        "enabled": True,
        "provider": "gcs",
        "bucket": "My-Bucket",
        "prefix": "opensre",
        "region": "",
        "profile": "",
    }
    assert config == RemoteSyncConfig(bucket="My-Bucket", provider="gcs")


def test_save_defaults_provider_and_prefix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    save_remote_sync_settings(RemoteSyncSetupRequest(bucket="b", provider="  ", prefix=" "))
    assert _on_disk_section(tmp_path)["provider"] == "aws"
    assert _on_disk_section(tmp_path)["prefix"] == "opensre"


def test_save_requires_bucket(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    with pytest.raises(RemoteSyncConfigError, match="bucket"):
        save_remote_sync_settings(RemoteSyncSetupRequest(bucket="  "))


def test_save_unknown_provider_lists_known_ones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    with pytest.raises(RemoteSyncConfigError, match="unknown remote-sync provider") as caught:
        save_remote_sync_settings(RemoteSyncSetupRequest(bucket="b", provider="gogle"))
    assert "aws" in str(caught.value)
    assert "gcs" in str(caught.value)
    assert "vercel" in str(caught.value)


def test_save_maps_settings_file_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_section: str, _values: dict[str, Any]) -> None:
        raise LocalSettingsError("config.yml is unreadable")

    monkeypatch.setattr(setup_mod, "update_section", _boom)
    with pytest.raises(RemoteSyncConfigError, match="unreadable"):
        save_remote_sync_settings(RemoteSyncSetupRequest(bucket="b"))


def test_disable_keeps_stored_settings_for_later(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    save_remote_sync_settings(RemoteSyncSetupRequest(bucket="b", provider="gcs"))

    disable_remote_sync()

    on_disk = _on_disk_section(tmp_path)
    assert on_disk["enabled"] is False
    # Provider/bucket survive, so re-enabling does not need a full setup.
    assert on_disk["provider"] == "gcs"
    assert on_disk["bucket"] == "b"


def test_disable_on_a_fresh_machine_writes_only_the_switch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    disable_remote_sync()
    assert _on_disk_section(tmp_path) == {"enabled": False}


def test_vercel_credential_hint_names_token_env() -> None:
    assert BLOB_READ_WRITE_TOKEN_ENV in credential_hint_for_provider("vercel")
    lines = format_setup_lines(RemoteSyncConfig(bucket="b", provider="vercel", prefix="p"))
    assert any(BLOB_READ_WRITE_TOKEN_ENV in line for line in lines)


def test_save_rejects_region_for_provider_that_does_not_use_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    with pytest.raises(RemoteSyncConfigError, match="--region is not used by provider 'vercel'"):
        save_remote_sync_settings(
            RemoteSyncSetupRequest(bucket="b", provider="vercel", region="us-east-1")
        )


def test_save_rejects_profile_for_provider_that_does_not_use_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    with pytest.raises(RemoteSyncConfigError, match="--profile is not used by provider 'vercel'"):
        save_remote_sync_settings(
            RemoteSyncSetupRequest(bucket="b", provider="vercel", profile="dev")
        )


def test_save_allows_region_and_profile_for_aws(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    config = save_remote_sync_settings(
        RemoteSyncSetupRequest(bucket="b", provider="aws", region="us-east-1", profile="dev")
    )
    assert config.region == "us-east-1"
    assert config.profile == "dev"


def test_gcs_credential_hint_points_at_application_default_credentials() -> None:
    hint = credential_hint_for_provider("gcs")
    assert "gcloud auth application-default login" in hint
    lines = format_setup_lines(RemoteSyncConfig(bucket="b", provider="gcs", prefix="opensre"))
    assert any("gcloud auth application-default login" in line for line in lines)


def test_aws_credential_hint_points_at_ambient_profile_or_sso() -> None:
    hint = credential_hint_for_provider("aws")
    assert "sso" in hint.lower() or "profile" in hint.lower()


def test_community_provider_gets_generic_hint() -> None:
    assert credential_hint_for_provider("some-community-backend") == (
        "Use ambient credentials for this provider; opensre does not store them."
    )


def test_format_setup_lines_confirms_values_hint_and_next_steps() -> None:
    lines = format_setup_lines(RemoteSyncConfig(bucket="b", provider="gcs", prefix="opensre"))
    assert lines[0] == "Remote sync settings saved → gcs / b/opensre"
    assert "Stored in ~/.opensre/config.yml" in lines[1]
    assert lines[3].startswith("Next: opensre remote-sync status")


def test_format_setup_lines_disabled_says_off_without_a_sync_suggestion() -> None:
    lines = format_setup_lines(
        RemoteSyncConfig(bucket="b", provider="gcs", prefix="opensre"), enabled=False
    )
    assert lines[0].startswith("Remote sync settings saved")
    assert "Remote sync is off" in lines[-1]
    assert "remote-sync sync" not in lines[-1]


def test_setup_validation_normalizes_without_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from infrastructure.filestorage.setup import validate_remote_sync_settings

    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    draft = RemoteSyncSetupRequest(bucket=" demo ", provider=" GCS ", prefix=" ")
    assert validate_remote_sync_settings(draft) == RemoteSyncSetupRequest(
        bucket="demo", provider="gcs"
    )
    assert draft.bucket == " demo "
    assert not (tmp_path / "config.yml").exists()

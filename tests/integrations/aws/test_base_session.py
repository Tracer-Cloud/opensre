"""``base_session`` — OpenSRE's own base identity for boto3, half-pair safe.

OpenSRE keeps ``AWS_SECRET_ACCESS_KEY`` keyring-only, so its ``.env`` loads
``AWS_ACCESS_KEY_ID`` alone into the process. boto3 reads the pair as one unit
and raises ``PartialCredentialsError`` on a lone id instead of falling through
to ``~/.aws`` — which broke the role mode for anyone who had ever set up static
keys. ``base_session`` pairs the id with the keyring secret when both resolve,
and otherwise drops boto3's ``env`` provider so the chain falls through.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import integrations.aws.env as aws_env


def _resolve_from_process_env(key: str, *, default: str = "") -> str:
    """Stand-in for the OpenSRE resolver: process env only, no keyring."""
    return os.environ.get(key, default)


def _resolve_nothing(_key: str, *, default: str = "") -> str:
    return default


@pytest.fixture
def isolated_aws_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """No real profiles or keys can leak in; a fake profile file is provided."""
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    creds = tmp_path / "credentials"
    creds.write_text("[default]\naws_access_key_id = AKIAFROMPROFILE\naws_secret_access_key = s\n")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(creds))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    # The OpenSRE resolver only sees the process env in these tests (no keyring).
    monkeypatch.setattr("config.llm_credentials.resolve_env_credential", _resolve_from_process_env)
    return creds


@pytest.mark.usefixtures("isolated_aws_env")
def test_a_lone_access_key_id_does_not_block_the_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reporter's machine: id from .env, secret in the keyring, profile in ~/.aws."""
    # Arrange — half a pair in the environment
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAHALFPAIRFROMDOTENV")

    # Act
    credentials = aws_env.base_session().get_credentials()

    # Assert — boto3 fell through to the profile instead of raising
    assert credentials is not None
    assert credentials.method == "shared-credentials-file"
    assert credentials.access_key == "AKIAFROMPROFILE"


@pytest.mark.usefixtures("isolated_aws_env")
def test_a_complete_pair_is_used_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    """When id and secret both resolve, that identity wins over any profile."""
    # Arrange
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")

    # Act
    credentials = aws_env.base_session().get_credentials()

    # Assert
    assert credentials is not None
    assert credentials.method == "explicit"
    assert credentials.access_key == "AKIAEXAMPLE"


def test_nothing_anywhere_means_no_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bare-laptop case the role-mode gate is built for."""
    # Arrange
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "nope"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "nope-config"))
    monkeypatch.setattr("config.llm_credentials.resolve_env_credential", _resolve_nothing)
    # Instance-metadata lookups must not run in a unit test
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")

    # Act
    from integrations.aws.credential_chain import has_ambient_credentials

    # Assert
    assert has_ambient_credentials() is False


def _provider_names(session: object) -> list[str]:
    resolver = session._session.get_component("credential_provider")  # type: ignore[attr-defined]
    return [p.METHOD for p in resolver.providers]


@pytest.mark.usefixtures("isolated_aws_env")
def test_local_only_drops_the_network_credential_providers() -> None:
    """The setup gate's pre-check must answer instantly on a laptop or behind a proxy.

    boto3's chain ends with the ECS container endpoint and EC2 instance
    metadata; on a machine that is neither, they wait out connect timeouts
    (measured: ~4s with IMDS blackholed) before saying "no credentials". The
    pre-check only decides whether to warn, so it looks at local sources only.
    """
    # Act
    names = _provider_names(aws_env.base_session(local_only=True))

    # Assert
    assert "container-role" not in names
    assert "iam-role" not in names
    assert "shared-credentials-file" in names  # local sources stay


@pytest.mark.usefixtures("isolated_aws_env")
def test_the_default_session_keeps_the_full_chain_for_real_calls() -> None:
    """On EC2/ECS the attached role *is* the credential; the STS call must find it."""
    # Act
    names = _provider_names(aws_env.base_session())

    # Assert
    assert "container-role" in names
    assert "iam-role" in names


def test_has_ambient_credentials_is_local_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange — record what base_session is asked for
    seen: dict[str, object] = {}

    class _NoCreds:
        def get_credentials(self) -> None:
            return None

    def _fake_base_session(*, region: str | None = None, local_only: bool = False) -> _NoCreds:
        seen["local_only"] = local_only
        return _NoCreds()

    monkeypatch.setattr(aws_env, "base_session", _fake_base_session)
    from integrations.aws.credential_chain import has_ambient_credentials

    # Act
    result = has_ambient_credentials()

    # Assert
    assert result is False
    assert seen == {"local_only": True}

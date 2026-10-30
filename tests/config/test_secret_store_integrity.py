"""Credential mutations must preserve a store that cannot be read safely."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.constants.secrets import OPENSRE_DISABLE_KEYRING_ENV
from config.secrets import local_file
from config.secrets.backend import KeyringUnavailableError
from config.secrets.store import delete_secret, resolve_secret, save_secret


@pytest.fixture
def credential_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "credentials.json"
    monkeypatch.delenv(OPENSRE_DISABLE_KEYRING_ENV, raising=False)
    for env_var in ("TEST_CREDENTIAL_A", "TEST_CREDENTIAL_B", "TEST_CREDENTIAL_NEW"):
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setattr(local_file, "store_path", lambda: path)
    local_file.clear_read_cache()
    return path


@pytest.mark.parametrize("operation", ["save", "delete"])
def test_transient_read_failure_preserves_all_credentials(
    credential_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    save_secret("TEST_CREDENTIAL_A", "dummy-a")
    save_secret("TEST_CREDENTIAL_B", "dummy-b")
    before = credential_path.read_bytes()
    original_read = Path.read_text

    def failed_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == credential_path:
            raise PermissionError("simulated transient credential read failure")
        return original_read(path, *args, **kwargs)  # type: ignore[arg-type]

    with monkeypatch.context() as read_failure:
        read_failure.setattr(Path, "read_text", failed_read)
        with pytest.raises(KeyringUnavailableError):
            if operation == "save":
                save_secret("TEST_CREDENTIAL_NEW", "dummy-new")
            else:
                delete_secret("TEST_CREDENTIAL_A")
    assert credential_path.read_bytes() == before
    assert resolve_secret("TEST_CREDENTIAL_A") == "dummy-a"
    assert resolve_secret("TEST_CREDENTIAL_B") == "dummy-b"


@pytest.mark.parametrize(
    "payload",
    [
        b'{"version":1,"secrets":{"TEST_CREDENTIAL_A":"dummy-a"},"unfinished":',
        b"\xff\xfe",
        b"[]",
        b'{"version":1,"secrets":[]}',
        b'{"version":1,"secrets":{"TEST_CREDENTIAL_A":"dummy-a","INVALID":5}}',
    ],
)
def test_damaged_store_is_not_replaced_when_saving_a_new_credential(
    credential_path: Path, payload: bytes
) -> None:
    credential_path.write_bytes(payload)
    before = credential_path.read_bytes()
    with pytest.raises(KeyringUnavailableError):
        save_secret("TEST_CREDENTIAL_NEW", "dummy-new")
    assert credential_path.read_bytes() == before


def test_missing_store_still_initializes_on_first_save(credential_path: Path) -> None:
    assert not credential_path.exists()
    save_secret("TEST_CREDENTIAL_NEW", "dummy-new")
    assert resolve_secret("TEST_CREDENTIAL_NEW") == "dummy-new"
    data = json.loads(credential_path.read_text(encoding="utf-8"))
    assert data["secrets"] == {"TEST_CREDENTIAL_NEW": "dummy-new"}

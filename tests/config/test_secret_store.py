"""Tier policy for secret storage: environment first, owner-only local file second.

The OS keychain is no longer a tier. What these tests pin is that the remaining
path still reaches a working state, and that logout genuinely revokes.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from config.constants.secrets import OPENSRE_DISABLE_KEYRING_ENV
from config.llm_auth.credentials import delete as delete_provider_auth
from config.llm_auth.credentials import resolve_for_request, save_api_key
from config.secrets import local_file
from config.secrets.backend import (
    KeyringUnavailableError,
    KeyringUnavailableReason,
    SecretTier,
)
from config.secrets.store import (
    delete_secret,
    lookup,
    resolve_secret,
    resolve_stored_secret,
    save_secret,
    secret_source,
)

_ENV_VAR = "OPENSRE_TEST_FALLBACK_TOKEN"


@pytest.fixture(autouse=True)
def _local_storage_enabled(tmp_path: Path, monkeypatch) -> None:
    """Undo the suite-wide disable switch so the storage policy is exercised."""
    monkeypatch.delenv(OPENSRE_DISABLE_KEYRING_ENV, raising=False)
    monkeypatch.delenv(_ENV_VAR, raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(local_file, "store_path", lambda: tmp_path / "credentials.json")


def _stored_contents() -> dict[str, str]:
    path = local_file.store_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))["secrets"]


def test_keyring_unavailable_reason_values_round_trip() -> None:
    """The reason enum still classifies why a *write* was refused."""
    expected = {
        "disabled": KeyringUnavailableReason.DISABLED,
        "no_backend": KeyringUnavailableReason.NO_BACKEND,
        "backend_error": KeyringUnavailableReason.BACKEND_ERROR,
    }

    assert {reason.value: reason for reason in KeyringUnavailableReason} == expected
    for value, reason in expected.items():
        assert KeyringUnavailableReason(value) == reason
        assert isinstance(reason, str)
        assert str(reason) == value


def test_a_saved_credential_is_resolvable_at_request_time() -> None:
    """A credential accepted during onboarding has to actually work afterwards."""
    save_secret(_ENV_VAR, "sk-headless")

    found = lookup(_ENV_VAR)

    assert found.value == "sk-headless"
    assert found.tier == "fallback"
    assert secret_source(_ENV_VAR) == "fallback"


def test_env_wins_over_the_stored_copy(monkeypatch) -> None:
    save_secret(_ENV_VAR, "sk-stored")
    monkeypatch.setenv(_ENV_VAR, "sk-from-env")

    found = lookup(_ENV_VAR)

    assert found.value == "sk-from-env"
    assert found.tier == "env"


def test_stored_secret_ignores_the_environment(monkeypatch) -> None:
    save_secret(_ENV_VAR, "sk-stored")
    monkeypatch.setenv(_ENV_VAR, "sk-from-env")

    assert resolve_stored_secret(_ENV_VAR) == "sk-stored"
    assert resolve_secret(_ENV_VAR) == "sk-from-env"


def test_lookup_tolerates_a_credential_file_lock_timeout(monkeypatch) -> None:
    """local_file.get must not raise through resolve/startup."""
    from filelock import Timeout

    def _locked(_name: str) -> str:
        raise Timeout("/tmp/credentials.json.lock")

    monkeypatch.setattr(local_file, "get", _locked)

    assert lookup(_ENV_VAR).value == ""
    assert lookup(_ENV_VAR).tier == SecretTier.NONE


def test_lookup_tolerates_local_store_error_that_is_not_an_oserror(monkeypatch) -> None:
    """LocalStoreError must be caught even though it does not subclass OSError."""
    assert not issubclass(local_file.LocalStoreError, OSError)

    def _locked(_name: str) -> str:
        raise local_file.LocalStoreError("lock timed out")

    monkeypatch.setattr(local_file, "get", _locked)

    assert lookup(_ENV_VAR).value == ""
    assert lookup(_ENV_VAR).tier == SecretTier.NONE


def test_save_maps_a_lock_timeout_to_unavailable(monkeypatch) -> None:
    def _locked(*_args: object, **_kwargs: object) -> None:
        raise local_file.LocalStoreError("lock timed out")

    monkeypatch.setattr(local_file, "set", _locked)

    with pytest.raises(KeyringUnavailableError) as excinfo:
        save_secret(_ENV_VAR, "sk-headless")

    assert excinfo.value.reason == KeyringUnavailableReason.NO_BACKEND


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_the_credential_file_is_never_readable_beyond_its_owner() -> None:
    save_secret(_ENV_VAR, "sk-headless")

    mode = stat.S_IMODE(local_file.store_path().stat().st_mode)

    assert mode == 0o600


def test_delete_clears_the_stored_copy() -> None:
    """Logout must not leave a copy that keeps resolving."""
    save_secret(_ENV_VAR, "sk-stored")

    delete_secret(_ENV_VAR)

    assert resolve_secret(_ENV_VAR) == ""
    assert _ENV_VAR not in _stored_contents()


def test_delete_raises_when_the_local_store_lock_times_out(monkeypatch) -> None:
    """Logout must not report success while the local credential still resolves."""
    save_secret(_ENV_VAR, "sk-stored")

    def _locked(_name: str) -> None:
        raise local_file.LocalStoreError("lock timed out")

    monkeypatch.setattr(local_file, "delete", _locked)

    with pytest.raises(KeyringUnavailableError) as excinfo:
        delete_secret(_ENV_VAR)

    assert excinfo.value.reason == KeyringUnavailableReason.BACKEND_ERROR
    assert _ENV_VAR in _stored_contents()


def test_provider_logout_clears_the_stored_copy(monkeypatch) -> None:
    """Regression: `opensre auth logout` reported success while the key still worked."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    save_api_key("deepseek", "sk-headless")
    assert resolve_for_request("deepseek").api_key == "sk-headless"

    delete_provider_auth("deepseek")

    assert resolve_for_request("deepseek").ok is False
    assert _stored_contents() == {}


def test_request_resolution_reports_the_stored_tier(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    save_api_key("deepseek", "sk-headless")

    resolution = resolve_for_request("deepseek")

    assert resolution.api_key == "sk-headless"
    assert resolution.source == "fallback"


def test_save_raises_when_the_file_refuses(monkeypatch) -> None:
    """A caller that sees no exception must be able to trust the write landed."""

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(local_file, "set", _refuse)

    with pytest.raises(KeyringUnavailableError) as excinfo:
        save_secret(_ENV_VAR, "sk-headless")

    assert excinfo.value.reason == KeyringUnavailableReason.NO_BACKEND


def test_disabling_local_storage_stays_fail_closed(monkeypatch) -> None:
    """OPENSRE_DISABLE_KEYRING opts out of persistence, not into a weaker tier."""
    monkeypatch.setenv(OPENSRE_DISABLE_KEYRING_ENV, "1")

    with pytest.raises(KeyringUnavailableError) as excinfo:
        save_secret(_ENV_VAR, "sk-disabled")

    assert excinfo.value.reason == KeyringUnavailableReason.DISABLED
    assert not Path(local_file.store_path()).exists()


def test_disabled_local_storage_reads_only_the_environment(monkeypatch) -> None:
    save_secret(_ENV_VAR, "sk-stored")
    monkeypatch.setenv(OPENSRE_DISABLE_KEYRING_ENV, "1")

    assert resolve_secret(_ENV_VAR) == ""

    monkeypatch.setenv(_ENV_VAR, "sk-from-env")
    assert resolve_secret(_ENV_VAR) == "sk-from-env"


def test_empty_value_clears_instead_of_storing() -> None:
    save_secret(_ENV_VAR, "sk-headless")

    save_secret(_ENV_VAR, "   ")

    assert resolve_secret(_ENV_VAR) == ""
    assert _ENV_VAR not in _stored_contents()


def test_unchanged_store_is_read_once_across_lookups(monkeypatch) -> None:
    """Startup resolves credentials per tool; an unchanged file must not be re-parsed each time."""
    # A store written moments ago is never cached; this one counts as settled.
    monkeypatch.setattr(local_file, "_RACY_WINDOW_NS", 0)
    save_secret(_ENV_VAR, "sk-stored")
    local_file.clear_read_cache()
    reads: list[Path] = []
    real_load = local_file._load_unlocked

    def _counting_load(path: Path) -> dict[str, str]:
        reads.append(path)
        return real_load(path)

    monkeypatch.setattr(local_file, "_load_unlocked", _counting_load)

    assert [resolve_secret(_ENV_VAR) for _ in range(5)] == ["sk-stored"] * 5
    assert len(reads) == 1


def test_a_store_replaced_by_another_process_is_read_again(monkeypatch) -> None:
    """The read cache must never hide a credential written outside this process."""
    monkeypatch.setattr(local_file, "_RACY_WINDOW_NS", 0)
    save_secret(_ENV_VAR, "sk-old")
    local_file.clear_read_cache()
    assert resolve_secret(_ENV_VAR) == "sk-old"

    # Another process publishes the same way local_file does: a new file swapped in.
    path = local_file.store_path()
    replacement = path.with_name("replacement.json")
    replacement.write_text(json.dumps({"version": 1, "secrets": {_ENV_VAR: "sk-new"}}))
    os.replace(replacement, path)

    assert resolve_secret(_ENV_VAR) == "sk-new"

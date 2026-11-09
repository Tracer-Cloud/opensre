"""``llm_ready`` refuses only a key that was never configured, not a stale or unreadable one."""

from __future__ import annotations

import pytest
from filelock import Timeout

from config.llm_auth.credentials import resolve_for_request
from config.llm_auth.records import resolve_provider_auth_record, save_provider_auth_record
from config.secrets.local_file import LocalStoreError
from core.llm.readiness import llm_ready


def _signed_out() -> None:
    return None


def _contended_read(_env_var: str) -> str:
    raise LocalStoreError("Reading the credential store failed.")


def _contended_metadata(_provider: str) -> dict[str, str]:
    raise Timeout("llm-auth.json.lock")


@pytest.fixture(autouse=True)
def _signed_out_deepseek(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAFANA_CONFIG_SKIP_ENV_FILE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("config.account.account_llm_route", _signed_out)


def test_stale_saved_key_proceeds_to_request_time_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a contended file read stales a stored key, and the preflight then refused it.

    Only request-time resolution re-reads the key and clears the flag, so refusing a
    stale key before the turn would block the user until they re-saved it.
    """
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    save_provider_auth_record(
        provider="deepseek",
        auth_name="deepseek",
        kind="api_key",
        source="fallback",
        detail="DEEPSEEK_API_KEY stored in the local credentials file.",
        env_var="DEEPSEEK_API_KEY",
    )
    monkeypatch.setattr("config.secrets.local_file.get", _contended_read)
    assert resolve_for_request("deepseek").ok is False
    assert resolve_provider_auth_record("deepseek")["stale"] == "true"

    assert llm_ready().ready is True


def test_unreadable_auth_status_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    # A lock timeout on the auth metadata says nothing about the key.
    monkeypatch.setattr(
        "config.llm_auth.credentials.resolve_provider_auth_record", _contended_metadata
    )

    assert llm_ready().ready is True


def test_never_configured_key_is_refused() -> None:
    readiness = llm_ready()

    assert readiness.ready is False
    assert readiness.provider == "deepseek"
